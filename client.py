import collections
import datetime
import http.client
import io
import itertools
import queue
import random
import requests
import threading
import time
import wx
import wx.lib.scrolledpanel

# TODO: Create compiled app for windows
# TODO: Add an overlay message when the area is not in focus (make a key command to bring it into focus?)
# TODO: Color text based on whether it gets sent successfully or not
# TODO: option to pull data from plover's log?
# TODO: save text to file?

class TextEntry:
    PENDING = 0
    SENT = 1
    SUCCESS = 2
    FAILED = 3
    
    def __init__(self, text=''):
        self.time = datetime.datetime.utcnow()
        self.text = text
        self.status = TextEntry.PENDING

class Client:
    def __init__(self):
        # constants
        self._heartbeat_interval = datetime.timedelta(seconds=5)
        self._retry_timeout = datetime.timedelta(seconds=5)
        self._poll_interval = 1
        self._post_timeout = 0.2
        
        # state
        self._confirmed = collections.deque()
        self._sent = collections.deque()
        self._pending = collections.deque()
        self._seq = 0
        self._retry_start = datetime.datetime.utcnow()
        self._retry_delay = 0
        self._last_post = datetime.datetime.utcnow() - self._heartbeat_interval
        self._correction = datetime.timedelta()

        # settings
        self.post_callback = lambda x: None
        self.url = ''
        self.delay = datetime.timedelta(seconds=5)
        self.offset = datetime.timedelta()
        
    def text(self, text):
        "Queue text to be sent."
        self._pending.append(TextEntry(text=text))
        
    def delete(self, spaces):
        "Delete text if it hasn't already been sent."
        while spaces and self._pending:
            item = self._pending[-1]
            count = min(spaces, len(item.text))
            if count > 0:
                item.text = item.text[:-count]
                spaces -= count
            if not item.text:
                self._pending.pop()

    def entries(self):
        return itertools.chain(self._confirmed, self._sent, self._pending)

    def _retry(self):
        if self._post(self._seq, self._sent):
            for item in self._sent:
                item.status = TextEntry.SUCCESS
            self._confirmed.extend(self._sent)
            self._sent.clear()
            return 0 if self._pending else self._poll_interval
        else:
            if datetime.datetime.utcnow() - self._retry_start >= self._retry_timeout:
                for item in self._sent:
                    item.status = TextEntry.FAILED
                self._confirmed.extend(self._sent)
                self._sent.clear()
                return 0 if self._pending else self._poll_interval
            self._retry_delay *= 2
            return random.uniform(0, self._retry_delay)

    def tick(self):
        """Runs background activities. Returns the delay, in seconds, until the next call to tick."""
        now = datetime.datetime.utcnow()
        if self._sent:
            return self._retry()
        
        self._seq += 1
        self._retry_start = datetime.datetime.utcnow()
        self._retry_delay = 0.1
        
        while self._pending and now - self._pending[0].time >= self.delay:
            item = self._pending.popleft()
            if item.text:
                self._sent.append(item)
        if self._sent:
            return self._retry()
        if now - self._last_post >= self._heartbeat_interval:
            self._post(self._seq, [TextEntry()])
        return self._poll_interval

    def _post(self, seq, items):
        headers = {'content-type': 'text/plain'}
        buf = io.StringIO(newline="\n")
        offset = self.offset
        for item in items:
            print((item.time + offset + self._correction).isoformat()[:-3], item.text.replace("\n", "<br>"), sep="\n", end="\n", file=buf, flush=True)
        data = buf.getvalue()
        try:
            r = requests.post(self.url + "&seq={seq}".format(seq=seq), data=data, headers=headers, timeout=self._post_timeout)
            r.raise_for_status()
            self._correction = datetime.datetime.strptime(r.text.strip(), "%Y-%m-%dT%H:%M:%S.%f") - datetime.datetime.utcnow()
            success = True
        except requests.exceptions.RequestException:
            success = False
        self._last_post = datetime.datetime.utcnow()
        self.post_callback(success)
        return success


def format_sent_text(items):
    return ''.join(['<span color="{color}>{text}</span>'.format({color: 'green' if item[0] else 'red', text: item[1].replace("&", "&amp;").replace("'", "&apos;").replace('"', '&quot;').replace('<', '&lt;').replace('>', '&gt;')}) for item in items])

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
        url.SetValue(self.client.url)
        
        hbox.Add(wx.StaticText(self, label="Delay: "), flag = wx.ALL, border=3)
        delay = wx.TextCtrl(self)
        hbox.Add(delay, border=3, flag=wx.ALL)
        delay.Bind(wx.EVT_TEXT, self.OnDelayChange)
        delay.SetValue(str(self.client.delay.total_seconds()))

        hbox.Add(wx.StaticText(self, label="Offset: "), flag = wx.ALL, border=3)
        offset = wx.TextCtrl(self)
        hbox.Add(offset, border=3, flag=wx.ALL)
        delay.Bind(wx.EVT_TEXT, self.OnOffsetChange)
        offset.SetValue(str(self.client.offset.total_seconds()))

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
        self.statusbar.SetStatusText("Disconnected")
        self.client.post_callback = lambda success: wx.CallAfter(self.OnStatus, success)
        self.Fit()
        self.Show(True)
        self.Tick()
        
    def _display(self):
        text = ''.join(item.text for item in self.client.entries())
        self.output.SetLabel(text)
        self.output.Wrap(self.GetSize().width)
        self.scroll.FitInside()
        self.scroll.Scroll(-1, self.scroll.GetClientSize().height)
        
    def OnChar(self, e):
        c = e.GetUnicodeKey()
        if c == wx.WXK_RETURN:
            self.client.text("\n")
        elif c == wx.WXK_BACK or c == wx.WXK_DELETE:
            self.client.delete(1)
        elif c != wx.WXK_NONE:
            self.client.text(chr(c))
        self._display()

    def Tick(self):
        delay = self.client.tick()
        self._display()
        if delay > 0:
            wx.CallLater(int(delay * 1000), self.Tick)
        else:
            wx.CallAfter(self.Tick)

    def OnURLChange(self, e):
        self.client.url = e.String.strip()
        
    def OnDelayChange(self, e):
        try:
            self.client.delay = datetime.timedelta(seconds=int(e.String.strip()))
        except ValueError:
            pass
        
    def OnOffsetChange(self, e):
        try:
            self.client.offset = datetime.timedelta(seconds=int(e.String.strip()))
        except ValueError:
            pass
        
    def OnStatus(self, success):
        if success:
            self.statusbar.SetStatusText("Connected")
        else:
            self.statusbar.SetStatusText("Disconnected")

def gui():
    app = wx.App(False)
    frame = MyFrame()
    app.MainLoop()

def client_test():
    c = Client()
    c.url = 'http://localhost:8080/?foo'
    def callback(success):
        if success:
            print("success")
        else:
            print("failed")
    c.post_callback = callback
    #c.url = "http://upload.youtube.com/closedcaption?itag=33&key=yt_qc&expire=1440296665&sparams=id%2Citag%2Cns%2Cexpire&signature=3CE301723686C033E58012110F9FE88BBF7CA679.BCD9169B4E6FD08795155F4827E01013FA13A9CC&ns=yt-ems-t&id=e3g9lbxmZ2SgGzG4KsjvDA1437704530287373"
    s = """Lorem ipsum dolor sit amet, cum fastidii perfecto legendos et, eu vocent efficiantur est, in reque appareat lucilius quo. Cu nibh illum pri. Id vim vero consequat consetetur. Quod suscipit intellegam nam ex, mel modo mazim animal ex. Ad vim timeam quaestio, quo paulo quaeque equidem ei. Vel ne zril adolescens voluptatum, numquam atomorum his ei. Ferri volutpat sea id, ad fuisset adipiscing vix.

    Sea porro intellegam ad, sint animal te mea, eum meis graeco apeirian ei. Choro veniam te usu. Eu fabulas torquatos usu. Dolorum sapientem eu eum, sed timeam suscipit no, detraxit pericula mei at. Cu soluta graeco usu. Id sit viderer appellantur, eos nemore timeam id.

    Has utamur admodum splendide id, iuvaret utroque meliore duo ad. Et quo nihil vitae volumus. Ut eum ludus vulputate. Nobis quaestio ne vel. An quo tation tritani. Tollit periculis concludaturque in pri, sea no choro fastidii complectitur.

    Mucius bonorum vis ad, usu ei oporteat repudiare. Eum ex nonumy doctus, quo omnis deleniti eu, ea qui recusabo quaerendum necessitatibus. Id qui wisi philosophia, assum eripuit vis at, usu cu adipisci invenire voluptatibus. Ex probo noster equidem eum, cu ferri possim per, id natum liberavisse vis. An nam graeco timeam deserunt.

    Ea probo assum inimicus sea, omnes admodum ius at. No eripuit labores propriae sed, consul civibus ea mei, nemore officiis ad sea. Sed minim equidem vituperatoribus no. Omnium virtute elaboraret vel ei."""
    words = iter(s.split())
    try:
        while True:
            while random.choice([True, False]):
                c.text(next(words) + " ")
            while random.choice([True, False]):
                c.delete(1)
            time.sleep(c.tick())
    except StopIteration:
        pass
        

if __name__ == "__main__":
    gui()
