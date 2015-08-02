import collections
import datetime
import http.client
import io
import itertools
import os.path
import queue
import random
import requests
import threading
import time
import wx
import wx.lib.scrolledpanel
import wx.lib.wordwrap

# TODO: Drop old text for long documents.
# TODO: handle shutdown without errors
# TODO: Create compiled app for windows
# TODO: Add an overlay message when the area is not in focus (make a key command to bring it into focus?)
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
        
    def __repr__(self):
        return "TextEntry({time}, {text}, {status})".format(time=self.time,text=self.text,status=self.status)

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
        self.offset = datetime.timedelta()
        
    def send(self, items):
        self._pending.extend(items)

    def entries(self):
        r = list(self._confirmed)
        r.extend(self._sent)
        r.extend(self._pending)
        return r

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
        
        self._sent.extend(self._pending)
        self._pending.clear()
        if self._sent:
            self._seq += 1
            self._retry_start = datetime.datetime.utcnow()
            self._retry_delay = 0.1
            for item in self._sent:
                item.status = TextEntry.SENT
            return self._retry()
        if now - self._last_post >= self._heartbeat_interval:
            self._seq += 1
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
        
class ColoredText:
    def __init__(self, text, color, bgcolor):
        self.text = text
        self.color = color
        self.bgcolor = bgcolor
        
    def __repr__(self):
        return "ColoredText({text}, {color}, {bgcolor})".format(text=self.text, color=self.color, bgcolor=self.bgcolor)
        
class ColoredStaticText(wx.Control):
    """ :class:`ColoredStaticText` allows text to be colored. """
    labelDelta = 1

    def __init__(self, parent, ID=-1, label="",
                 pos=wx.DefaultPosition, size=wx.DefaultSize,
                 style=0,
                 name="genstattext"):
        """
        Default class constructor.

        :param `parent`: parent window, must not be ``None``;
        :param integer `ID`: window identifier. A value of -1 indicates a default value;
        :param string `label`: the static text label (i.e., its text label);
        :param `pos`: the control position. A value of (-1, -1) indicates a default position,
         chosen by either the windowing system or wxPython, depending on platform;
        :param `size`: the control size. A value of (-1, -1) indicates a default size,
         chosen by either the windowing system or wxPython, depending on platform;
        :param integer `style`: the underlying :class:`Control` style;
        :param string `name`: the widget name.

        :type parent: :class:`Window`
        :type pos: tuple or :class:`Point`
        :type size: tuple or :class:`Size`
        """

        wx.Control.__init__(self, parent, ID, pos, size, style|wx.NO_BORDER,
                             wx.DefaultValidator, name)

        self.label = [ColoredText(label, "black", "white")]
        wx.Control.SetLabel(self, label) # don't check wx.ST_NO_AUTORESIZE yet
        self.InheritAttributes()
        self.SetInitialSize(size)

        self.Bind(wx.EVT_PAINT, self.OnPaint)
        self.SetBackgroundStyle(wx.BG_STYLE_PAINT)
        self._bgpen = wx.ThePenList.FindOrCreatePen("white")
        self._bgbrush = wx.TheBrushList.FindOrCreateBrush("white")

    def SetLabel(self, label):
        """
        label is a sequence of pairs of color and string.
        """
        self.label = label
        wx.Control.SetLabel(self, ''.join(item.text for item in label))
        style = self.GetWindowStyleFlag()
        self.InvalidateBestSize()
        if not style & wx.ST_NO_AUTORESIZE:
            self.SetSize(self.GetBestSize())
        self.Refresh()

    def SetFont(self, font):
        """
        Sets the static text font and updates the control's size to exactly
        fit the label unless the control has wx.ST_NO_AUTORESIZE flag.

        :param Font `font`: a valid font instance, which will be the new font used
         to display the text.
        """
        
        wx.Control.SetFont(self, font)
        style = self.GetWindowStyleFlag()
        self.InvalidateBestSize()
        if not style & wx.ST_NO_AUTORESIZE:
            self.SetSize(self.GetBestSize())
        self.Refresh()


    def DoGetBestSize(self):
        """
        Overridden base class virtual.  Determines the best size of
        the control based on the label size and the current font.

        .. note:: Overridden from :class:`Control`.
        """
        
        label = self.GetLabel()
        font = self.GetFont()
        if not font:
            font = wx.SystemSettings.GetFont(wx.SYS_DEFAULT_GUI_FONT)
        dc = wx.ClientDC(self)
        dc.SetFont(font)
        
        maxWidth = totalHeight = 0
        for line in label.split('\n'):
            if line == '':
                w, h = dc.GetTextExtent('W')  # empty lines have height too
            else:
                w, h = dc.GetTextExtent(line)
            totalHeight += h
            maxWidth = max(maxWidth, w)
        best = wx.Size(maxWidth, totalHeight)
        self.CacheBestSize(best)
        return best


    def Enable(self, enable=True):
        """
        Enable or disable the widget for user input. 

        :param bool `enable`: If ``True``, enables the window for input. If ``False``, disables the window.

        :returns: ``True`` if the window has been enabled or disabled, ``False`` if nothing was
         done, i.e. if the window had already been in the specified state.

        .. note:: Note that when a parent window is disabled, all of its children are disabled as
           well and they are reenabled again when the parent is.

        .. note:: Overridden from :class:`Control`.
        """

        retVal = wx.Control.Enable(self, enable)
        self.Refresh()

        return retVal
    

    def Disable(self):
        """
        Disables the control.

        :returns: ``True`` if the window has been disabled, ``False`` if it had been
         already disabled before the call to this function.
         
        .. note:: This is functionally equivalent of calling :meth:`~Control.Enable` with a ``False`` flag.

        .. note:: Overridden from :class:`Control`.
        """

        retVal = wx.Control.Disable(self)
        self.Refresh()

        return retVal


    def AcceptsFocus(self):
        """
        Can this window be given focus by mouse click?

        .. note:: Overridden from :class:`Control`.
        """

        return True


    def GetDefaultAttributes(self):
        """
        Overridden base class virtual.  By default we should use
        the same font/colour attributes as the native :class:`StaticText`.

        .. note:: Overridden from :class:`Control`.
        """
        
        return wx.StaticText.GetClassDefaultAttributes()


    def ShouldInheritColours(self):
        """
        Overridden base class virtual.  If the parent has non-default
        colours then we want this control to inherit them.

        .. note:: Overridden from :class:`Control`.
        """

        return True

    
    def OnPaint(self, event):
        """
        Handles the ``wx.EVT_PAINT``.

        :param `event`: a :class:`PaintEvent` event to be processed.
        """
        
        width, height = self.GetClientSize()
        if not width or not height:
            return
            
        dc = wx.AutoBufferedPaintDC(self)
        dc.SetPen(self._bgpen)
        dc.SetBrush(self._bgbrush)
        dc.DrawRectangle(0, 0, width, height)
            
        dc.SetFont(self.GetFont())
        dc.SetBackgroundMode(wx.SOLID)
        style = self.GetWindowStyleFlag()
        x = y = 0
        lines = []
        line = []
        for part in self.label:
            text = part.text
            while text:
                before, sep, text = text.partition('\n')
                line.append(ColoredText(before, part.color, part.bgcolor))
                if sep:
                    lines.append(line)
                    line = []
        if line:
            lines.append(line)
        y = 0
        _, emptylineheight = self.GetTextExtent('W')
        for line in lines:
            x = 0
            text = ''.join(item.text for item in line)
            if not text:
                y += emptylineheight
            else:
                linewidth, lineheight = self.GetTextExtent(text)
                if style & wx.ALIGN_RIGHT:
                    x = width - linewidth
                elif style & wx.ALIGN_CENTER:
                    x = (width - linewidth)/2
                for item in line:
                    dc.SetTextForeground(item.color)
                    dc.SetTextBackground(item.bgcolor)
                    dc.DrawText(item.text, x, y)
                    w, _ = self.GetTextExtent(item.text)
                    x += w
                y += lineheight

    def OnEraseBackground(self, event):
        """
        Handles the ``wx.EVT_ERASE_BACKGROUND`` event.

        :param `event`: a :class:`EraseEvent` event to be processed.

        .. note:: This is intentionally empty to reduce flicker.
        """

        pass
        
    def Wrap(self, width):
        label = self.GetLabel()
        font = self.GetFont()
        if not font:
            font = wx.SystemSettings.GetFont(wx.SYS_DEFAULT_GUI_FONT)
        dc = wx.ClientDC(self)
        dc.SetFont(font)
        newlabel = wx.lib.wordwrap.wordwrap(label, width, dc)
        if label != newlabel:
            currindex = 0
            offset = 0
            for c in newlabel:
                if currindex >= len(self.label):
                    break
                curr = self.label[currindex]
                if c != curr.text[offset]:
                    curr.text = curr.text[:offset] + c + curr.text[offset:]
                offset += 1
                if offset >= len(curr.text):
                    currindex += 1
                    offset = 0
        self.SetLabel(self.label)

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
        
        self._delay = datetime.timedelta(seconds=5)
        hbox.Add(wx.StaticText(self, label="Delay: "), flag = wx.ALL, border=3)
        delay = wx.TextCtrl(self)
        hbox.Add(delay, border=3, flag=wx.ALL)
        delay.Bind(wx.EVT_TEXT, self.OnDelayChange)
        delay.SetValue(str(self._delay.total_seconds()))

        hbox.Add(wx.StaticText(self, label="Offset: "), flag = wx.ALL, border=3)
        offset = wx.TextCtrl(self)
        hbox.Add(offset, border=3, flag=wx.ALL)
        delay.Bind(wx.EVT_TEXT, self.OnOffsetChange)
        offset.SetValue(str(self.client.offset.total_seconds()))

        self.scroll = wx.lib.scrolledpanel.ScrolledPanel(self, size=(300, 300))
        self.scroll.SetBackgroundColour("white")
        vbox.Add(self.scroll, proportion=3, flag = wx.EXPAND | wx.ALL, border=3)
        scrollvbox = wx.BoxSizer(wx.VERTICAL)
        self.output = ColoredStaticText(self.scroll)

        scrollvbox.Add(self.output, flag=wx.EXPAND)
        self.scroll.SetSizer(scrollvbox)
        self.scroll.SetAutoLayout(True)
        self.scroll.SetupScrolling(scroll_x=False)

        self._pending = collections.deque()
        self.input = wx.TextCtrl(self, style=wx.TE_MULTILINE)
        self.input.Bind(wx.EVT_TEXT, self.OnText)
        vbox.Add(self.input, proportion=1, flag=wx.EXPAND | wx.ALL, border=3)

        self.statusbar = self.CreateStatusBar()
        self.statusbar.SetStatusText("Disconnected")
        self.client.post_callback = lambda success: wx.CallAfter(self.OnStatus, success)
        self.Fit()
        self.Bind(wx.EVT_ACTIVATE, lambda x: self.input.SetFocus())
        self.Bind(wx.EVT_SIZE, lambda x: (x.Skip(), self._display()))
        self.Show(True)
        self.Tick()
        
    def _display(self):
        colormap = {TextEntry.PENDING: "white", TextEntry.SENT: "light gray", TextEntry.SUCCESS: "green", TextEntry.FAILED: "red"}
        label = [ColoredText(item.text, "black", colormap[item.status]) for item in self.client.entries()]
        collapsed = []
        for item in label:
            if not collapsed:
                collapsed.append(item)
            elif collapsed[-1].color != item.color or collapsed[-1].bgcolor != item.bgcolor:
                collapsed.append(item)
            else:
                collapsed[-1].text += item.text
        label = collapsed
        self.output.SetLabel(label)
        self.output.Wrap(self.scroll.GetSize().width)
        self.scroll.FitInside()
        self.scroll.Scroll(-1, self.scroll.GetClientSize().height)
        
    def OnText(self, e):
        print("value:", self.input.GetValue())
        text = self.input.GetValue()
        pending = self._pending
        self._pending = collections.deque()
        for item in pending:
            piece = text[:len(item.text)]
            text = text[len(item.text):]
            if item.text == piece:
                self._pending.append(item)
            else:
                common = os.path.commonprefix([item.text, piece])
                if common:
                    prefix = TextEntry()
                    prefix.time = item.time
                    prefix.text = common
                    self._pending.append(prefix)
                text = piece[len(common):] + text
                if text:
                    suffix = TextEntry()
                    suffix.text = text
                    self._pending.append(suffix)
                    text = ''
                break
        if text:
            self._pending.append(TextEntry(text))
        # Sanity check
        a = self.input.GetValue()
        b = ''.join(item.text for item in self._pending)
        if a != b:
            print("WTF!", a, b)

    def Tick(self):
        print("Tick")
        now = datetime.datetime.utcnow()
        allowedlength = len(self.input.GetRange(0, self.input.GetInsertionPoint()))
        tosend = []
        while self._pending and now - self._pending[0].time >= self._delay and len(self._pending[0].text) <= allowedlength:
            item = self._pending.popleft()
            tosend.append(item)
            allowedlength -= len(item.text)
        if tosend:
            self.client.send(tosend)
            pos = self.input.GetInsertionPoint()
            value = self.input.GetValue()
            endpos = self.input.GetLastPosition()
            toremove = ''.join(item.text for item in tosend)
            offset = len(toremove)
            newvalue = value[offset:]
            pos -= offset
            if len(value) != endpos: # This means newlines are two chars.
                pos -= toremove.count('\n')
            self.input.ChangeValue(newvalue)
            self.input.SetInsertionPoint(pos)
        
        delay = int(self.client.tick() * 1000)
        self._display()
        if delay > 0:
            # It seems like this can be garbage collected, contrary to the docs.
            # So we need to hold a reference to it until it runs.
            self._tick = wx.CallLater(delay, self.Tick)
        else:
            wx.CallAfter(self.Tick)

    def OnURLChange(self, e):
        self.client.url = e.String.strip()
        
    def OnDelayChange(self, e):
        try:
            self._delay = datetime.timedelta(seconds=int(e.String.strip()))
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
