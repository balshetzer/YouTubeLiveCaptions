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
import wx.lib.wordwrap

# TODO: Create compiled app for windows
# TODO: Add an overlay message when the area is not in focus (make a key command to bring it into focus?)
# TODO: Color text based on whether it gets sent successfully or not
# TODO: option to pull data from plover's log?
# TODO: save text to file?
# TODO handle size change on frame to re-layout text

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
        
        self._seq += 1
        self._retry_start = datetime.datetime.utcnow()
        self._retry_delay = 0.1
        
        while self._pending and now - self._pending[0].time >= self.delay:
            item = self._pending.popleft()
            if item.text:
                self._sent.append(item)
        if self._sent:
            for item in self._sent:
                item.status = TextEntry.SENT
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

BUFFERED = 0   # In unbuffered mode we can let the theme shine through,
               # otherwise we draw the background ourselves.

if wx.Platform == "__WXMAC__":
    try:
        from Carbon.Appearance import kThemeBrushDialogBackgroundActive
    except ImportError:
        kThemeBrushDialogBackgroundActive = 1
        
#----------------------------------------------------------------------

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

        wx.Control.SetLabel(self, label) # don't check wx.ST_NO_AUTORESIZE yet
        self.InheritAttributes()
        self.SetInitialSize(size)

        self.Bind(wx.EVT_PAINT, self.OnPaint)
        if BUFFERED:
            self.defBackClr = self.GetBackgroundColour()
            self.Bind(wx.EVT_ERASE_BACKGROUND, self.OnEraseBackground)
        else:
            self.SetBackgroundStyle(wx.BG_STYLE_SYSTEM)
        self.colors = [("black", 0)]

    def SetLabelAndColors(self, label, colors):
        self.Freeze()
        self.SetLabel(label)
        self.SetColors(colors)
        self.Thaw()

    def SetColors(self, colors):
        if len(colors) == 0:
            self.colors = [("black", 0)]
        elif colors[0][1] != 0:
            self.colors = [("black", 0)] + colors
        else:
            self.colors = colors
        self.colors.sort(key=lambda x: x[1])
        self.Refresh()

    def SetLabel(self, label):
        """
        Sets the static text label and updates the control's size to exactly
        fit the label unless the control has wx.ST_NO_AUTORESIZE flag.

        :param string `label`: the static text label (i.e., its text label).
        """
        
        wx.Control.SetLabel(self, label)
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

        return False


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
        
        if BUFFERED:
            dc = wx.BufferedPaintDC(self)
        else:
            dc = wx.PaintDC(self)
        width, height = self.GetClientSize()
        if not width or not height:
            return

        if BUFFERED:
            clr = self.GetBackgroundColour()
            if wx.Platform == "__WXMAC__" and clr == self.defBackClr:
                # if colour is still the default then use the theme's  background on Mac
                themeColour = wx.MacThemeColour(kThemeBrushDialogBackgroundActive)
                backBrush = wx.Brush(themeColour)
            else:
                backBrush = wx.Brush(clr, wx.BRUSHSTYLE_SOLID)
            dc.SetBackground(backBrush)
            dc.Clear()

        if self.IsEnabled():
            dc.SetTextForeground(self.GetForegroundColour())
        else:
            dc.SetTextForeground(wx.SystemSettings.GetColour(wx.SYS_COLOUR_GRAYTEXT))
            
        dc.SetFont(self.GetFont())
        label = self.GetLabel()
        style = self.GetWindowStyleFlag()
        x = y = 0
        pieces = []
        colorindex = 0
        textindex = 0
        for line in label.split('\n'):
            if not line:
                w, h = self.GetTextExtent('W') # empty lines have height too
                y += h
                textindex += 1
            else:
                w, h = self.GetTextExtent(line)
                if style & wx.ALIGN_RIGHT:
                    x = width - w
                if style & wx.ALIGN_CENTER:
                    x = (width - w)/2
                while line:
                    nextcolorindex = colorindex
                    end = len(line)
                    if colorindex + 1 < len(self.colors) and textindex + len(line) > self.colors[colorindex+1][1]:
                        end = self.colors[colorindex+1][1] - textindex
                        nextcolorindex = colorindex + 1
                    piece = line[:end]
                    line = line[end:]
                    pieces.append((x, y, self.colors[colorindex][0], piece))
                    w, _ = self.GetTextExtent(piece)
                    x += w
                    textindex += len(piece)
                    colorindex = nextcolorindex
                y += h
                x = 0
                textindex += 1
        for piece in pieces:
            dc.SetTextForeground(piece[2])
            dc.DrawText(piece[3], piece[0], piece[1])

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
        label = wx.lib.wordwrap.wordwrap(label, width, dc)
        self.SetLabel(label)

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
        #self.output = wx.StaticText(self.scroll)
        self.output = ColoredStaticText(self.scroll)
        self.output.SetBackgroundColour("white")
        self.output.SetForegroundColour("black")
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
        colormap = {TextEntry.PENDING: "black", TextEntry.SENT: "gray", TextEntry.SUCCESS: "green", TextEntry.FAILED: "red"}
        textlength = 0
        colors = []
        entries = self.client.entries()
        for item in entries:
            colors.append((colormap[item.status], textlength))
            textlength += len(item.text)
        text = ''.join(item.text for item in entries)
        print(text)
        print(colors)
        self.output.SetLabelAndColors(text, colors)
        self.output.Wrap(self.scroll.GetSize().width)
        self.scroll.FitInside()
        self.scroll.Scroll(-1, self.scroll.GetClientSize().height)
        
    def OnChar(self, e):
        print("event")
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
