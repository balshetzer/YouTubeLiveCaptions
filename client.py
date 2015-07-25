import collections
import datetime
import http.client
import io
import queue
import random
import requests
import threading
import time
import wx
import wx.lib.scrolledpanel

class Client:
    def __init__(self):
        self._queue = queue.Queue()
        self._delayed = queue.Queue()
        self.url = ''
        self.delay = datetime.timedelta(seconds=5)
        self.offset = datetime.timedelta()
        self.callback = None
        self.heartbeat_interval_seconds = 5
        self._correction = datetime.timedelta()
        threading.Thread(target=self._delayer, daemon=True).start()
        threading.Thread(target=self._poster, daemon=True).start()
        
    def send(self, message):
        timecode = datetime.datetime.utcnow()
        self._queue.put([timecode, message, 0])
        
    def delete(self, spaces):
        timecode = datetime.datetime.utcnow()
        self._queue.put([timecode, '', spaces])
        
    def _delayer(self):
        items = collections.deque()
        while True:
            if len(items) == 0:
                items.append(self._queue.get())
            if len(items[0][1]) == 0:
                items.popleft()
                continue
            diff = datetime.datetime.utcnow() - items[0][0]
            while diff < self.delay:
                delay = self.delay - diff
                time.sleep(delay.total_seconds())
                diff = datetime.datetime.utcnow() - items[0][0]
            try:
                while True:
                    items.append(self._queue.get_nowait())
            except queue.Empty:
                pass
            delete = 0
            for item in reversed(items):
                delete += item[2]
                count = min(delete, len(item[1]))
                if count > 0:
                    item[1] = item[1][:-count]
                    delete -= count
            if len(items[0][1]) > 0 and datetime.datetime.utcnow() - items[0][0] >= self.delay:
                self._delayed.put(items.popleft())

    def _poster(self):
        headers = {'content-type': 'text/plain'}
        seq = 1
        correction = datetime.timedelta()
        while True:
            try:
                item = self._delayed.get(timeout=self.heartbeat_interval_seconds)
            except queue.Empty:
                item = [datetime.datetime.utcnow(), '', 0]
            items = [item]
            try:
                for i in range(100):
                    items.append(self._delayed.get_nowait())
            except queue.Empty:
                pass
            buf = io.StringIO(newline="\r\n")
            offset = self.offset
            for item in items:
                print((item[0] + offset + correction).isoformat()[:-3], item[1].replace("\n", "<br>"), sep="\n", end="\n", file=buf, flush=True)
            data = buf.getvalue()
            backoff = 0.1
            start = time.time()
            timeout = 5  # seconds
            success = False
            while time.time() < start + timeout:
                try:
                    r = requests.post(self.url + "&seq={seq}".format(**locals()), data=data, headers=headers, timeout=1)
                    r.raise_for_status()
                    correction = datetime.datetime.strptime(r.text.strip(), "%Y-%m-%dT%H:%M:%S.%f") - datetime.datetime.utcnow()
                    success = True
                    break
                except requests.exceptions.RequestException as e:
                    time.sleep(random.uniform(0, backoff))
                    backoff *= 2
                    continue
            if self.callback:
                self.callback(success, ''.join([item[1] for item in items]))
            seq += 1

class MyFrame(wx.Frame):
    def __init__(self, parent=None):
        super().__init__(parent, title="Plover Captions for YouTube Live")

        self.client = Client()

        vbox = wx.BoxSizer(wx.VERTICAL)
        self.SetSizer(vbox)
        
        hbox = wx.BoxSizer(wx.HORIZONTAL)
        vbox.Add(hbox, flag=wx.ALL, border=3)
        
        hbox.Add(wx.StaticText(self, label="URL: "), flag=wx.ALL, border=3)
        url = wx.TextCtrl(self)
        hbox.Add(url, border=3, flag=wx.ALL)
        url.Bind(wx.EVT_TEXT, self.OnURLChange)
        
        hbox.Add(wx.StaticText(self, label="Delay: "), flag = wx.ALL, border=3)
        delay = wx.TextCtrl(self)
        hbox.Add(delay, border=3, flag=wx.ALL)
        delay.Bind(wx.EVT_TEXT, self.OnDelayChange)
        delay.SetValue('5')

        hbox.Add(wx.StaticText(self, label="Offset: "), flag = wx.ALL, border=3)
        offset = wx.TextCtrl(self)
        hbox.Add(offset, border=3, flag=wx.ALL)
        delay.Bind(wx.EVT_TEXT, self.OnOffsetChange)
        offset.SetValue('0')

        self.scroll = wx.lib.scrolledpanel.ScrolledPanel(self, size=(300, 300))
        self.scroll.SetBackgroundColour("white")
        vbox.Add(self.scroll, proportion=1, flag = wx.EXPAND | wx.ALL, border=3)
        scrollvbox = wx.BoxSizer(wx.VERTICAL)
        self.output = wx.StaticText(self.scroll)
        self.output.SetBackgroundColour("white")
        scrollvbox.Add(self.output, flag=wx.EXPAND)
        self.scroll.SetSizer(scrollvbox)
        self.scroll.SetAutoLayout(True)
        self.scroll.SetupScrolling(scroll_x=False)
        self.scroll.Bind(wx.EVT_CHAR, self.OnChar)

        self.statusbar = self.CreateStatusBar()
        self.client.callback = self.OnStatus
        self.Fit()
        self.Show(True)
        
    def OnChar(self, e):
        c = e.GetUnicodeKey()
        if c == wx.WXK_RETURN:
            self.output.SetLabel(self.output.GetLabel() + "\n")
            self.client.send("\n")
        elif c == wx.WXK_BACK or c == wx.WXK_DELETE:
            self.output.SetLabel(self.output.GetLabel()[:-1])
            self.client.delete(1)
        elif c != wx.WXK_NONE:
            self.output.SetLabel(self.output.GetLabel() + chr(c))
            self.client.send(chr(c))

        self.output.Wrap(self.GetSize().width)
        self.scroll.FitInside()
        self.scroll.Scroll(-1, self.scroll.GetClientSize().height)

    def OnURLChange(self, e):
        self.client.url = e.String.strip()
        
    def OnDelayChange(self, e):
        try:
            self.client.delay = datetime.timedelta(seconds=int(e.String.strip()))
            print(self.client.delay)
        except ValueError:
            pass
        
    def OnOffsetChange(self, e):
        try:
            self.client.offset = datetime.timedelta(seconds=int(e.String.strip()))
            print(self.client.offset)
        except ValueError:
            pass
        
    def OnStatus(self, success, text):
        if success:
            self.statusbar.SetStatusText("Connected")
        else:
            self.statusbar.SetStatusText("Disconnected")

if __name__ == "__main__":
    app = wx.App(False)
    frame = MyFrame()
    app.MainLoop()
