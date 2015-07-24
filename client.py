import datetime
import http.client
import queue
import io
import requests
import time
import random
import threading
import collections

class Client:
    def __init__(self):
        self._queue = queue.Queue()
        self._delayed = queue.Queue()
        self.url = ''
        self.delay = datetime.timedelta(seconds=1)
        self.offset = datetime.timedelta()
        self.callback = None
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
            item = self._delayed.get()
            items = [item]
            try:
                for i in range(100):
                    items.append(self._delayed.get_nowait())
            except queue.Empty:
                pass
            buf = io.StringIO(newline="\r\n")
            offset = self.offset
            for item in items:
                print((item[0] + offset + correction).isoformat()[:-3], item[1], sep="\n", end="\n", file=buf, flush=True)
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

c = Client()
c.url = 'http://localhost:8080/?foo'
def callback(success, message):
    if success:
        print("success:", message)
    else:
        print("failed:", message)
c.callback = callback
#c.url = "http://upload.youtube.com/closedcaption?itag=33&key=yt_qc&expire=1440296665&sparams=id%2Citag%2Cns%2Cexpire&signature=3CE301723686C033E58012110F9FE88BBF7CA679.BCD9169B4E6FD08795155F4827E01013FA13A9CC&ns=yt-ems-t&id=e3g9lbxmZ2SgGzG4KsjvDA1437704530287373"
s = """Lorem ipsum dolor sit amet, cum fastidii perfecto legendos et, eu vocent efficiantur est, in reque appareat lucilius quo. Cu nibh illum pri. Id vim vero consequat consetetur. Quod suscipit intellegam nam ex, mel modo mazim animal ex. Ad vim timeam quaestio, quo paulo quaeque equidem ei. Vel ne zril adolescens voluptatum, numquam atomorum his ei. Ferri volutpat sea id, ad fuisset adipiscing vix.

Sea porro intellegam ad, sint animal te mea, eum meis graeco apeirian ei. Choro veniam te usu. Eu fabulas torquatos usu. Dolorum sapientem eu eum, sed timeam suscipit no, detraxit pericula mei at. Cu soluta graeco usu. Id sit viderer appellantur, eos nemore timeam id.

Has utamur admodum splendide id, iuvaret utroque meliore duo ad. Et quo nihil vitae volumus. Ut eum ludus vulputate. Nobis quaestio ne vel. An quo tation tritani. Tollit periculis concludaturque in pri, sea no choro fastidii complectitur.

Mucius bonorum vis ad, usu ei oporteat repudiare. Eum ex nonumy doctus, quo omnis deleniti eu, ea qui recusabo quaerendum necessitatibus. Id qui wisi philosophia, assum eripuit vis at, usu cu adipisci invenire voluptatibus. Ex probo noster equidem eum, cu ferri possim per, id natum liberavisse vis. An nam graeco timeam deserunt.

Ea probo assum inimicus sea, omnes admodum ius at. No eripuit labores propriae sed, consul civibus ea mei, nemore officiis ad sea. Sed minim equidem vituperatoribus no. Omnium virtute elaboraret vel ei."""
words = s.split()
for word in words:
    time.sleep(0.5)
    c.send(word + " ")
    time.sleep(0.1)
    c.delete(random.randint(0, 3))
