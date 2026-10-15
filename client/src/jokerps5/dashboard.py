"""Local-first web dashboard for Joker PS5 Control."""

from __future__ import annotations

import hashlib
import json
import secrets
from collections.abc import Callable
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, urlparse

from .admin import AdminManager
from .client import JokerClient

MAX_TEXT_BYTES = 2 * 1024 * 1024
MAX_UPLOAD_BYTES = 512 * 1024 * 1024

HTML = r"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Joker PS5 Toolkit</title><style>
:root{color-scheme:dark;font:15px system-ui;background:#080b12;color:#edf4ff}*{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at 80% 0,#172b50,#080b12 42%)}header,main{max-width:1180px;margin:auto;padding:24px}header{display:flex;justify-content:space-between;align-items:center}.brand{font-size:24px;font-weight:800}.live{color:#62e6a7}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:16px}.card{background:#111827ed;border:1px solid #263650;border-radius:16px;padding:18px;box-shadow:0 16px 40px #0006}h2{margin:0 0 12px;font-size:16px;color:#8fc8ff}button,input,textarea{background:#18243a;color:#edf4ff;border:1px solid #365171;border-radius:9px;padding:9px 12px}button{cursor:pointer}button:hover{border-color:#59aaff}.danger{border-color:#75394a}.row{display:flex;gap:8px;flex-wrap:wrap}.metric{font-size:28px;font-weight:700}.muted{color:#99a8bb}.list{max-height:360px;overflow:auto}.item{display:flex;align-items:center;justify-content:space-between;gap:8px;padding:8px;border-bottom:1px solid #243047}.wide{grid-column:1/-1}code{color:#79d7ff}textarea{width:100%;height:320px;font:13px ui-monospace,monospace;resize:vertical}dialog{width:min(900px,94vw);background:#111827;color:#edf4ff;border:1px solid #365171;border-radius:14px;padding:18px}#toast{position:fixed;right:20px;bottom:20px;background:#173527;padding:12px 16px;border-radius:10px;display:none}
</style></head><body><header><div><div class="brand">Joker PS5 Toolkit</div><div class="muted">Filesystem, apps, controller, deployment and diagnostics</div></div><div id="connection">connecting</div></header><main class="grid">
<section class="card"><h2>Console</h2><div id="console"></div></section><section class="card"><h2>Storage</h2><div id="storage"></div></section>
<section class="card"><h2>Controller</h2><div class="row"><button onclick="post('/api/pad/open')">Open</button><button onclick="post('/api/pad/close')">Close</button><button onclick="press('up')">Up</button><button onclick="press('down')">Down</button><button onclick="press('left')">Left</button><button onclick="press('right')">Right</button><button onclick="press('cross')">Cross</button><button onclick="press('circle')">Circle</button><button onclick="post('/api/home')">Home</button></div><pre id="pad"></pre></section>
<section class="card"><h2>Notification</h2><div class="row"><input id="note" placeholder="Message" style="flex:1"><button onclick="notifyConsole()">Send</button></div></section>
<section class="card wide"><h2>Screen Capture</h2><div class="row"><select id="shotBackend"><option value="create">New whole-screen capture</option><option value="latest">Newest gallery capture</option><option value="grab">Running-title frame</option></select><input id="shotRequest" placeholder="/data/homebrew/TITLE/devloop-shot" style="flex:1"><input id="shotFolder" placeholder="/data/homebrew/TITLE/screenshots" style="flex:1"><button onclick="captureScreen()">Capture</button></div><img id="screen" style="display:none;max-width:100%;margin-top:12px;border-radius:10px" alt="PS5 capture"></section>
<section class="card wide"><h2>Installed Apps</h2><input id="filter" placeholder="Filter apps" oninput="renderApps()"><div id="apps" class="list"></div></section>
<section class="card wide"><h2>Filesystem Workspace</h2><div class="row"><input id="path" value="/data" style="flex:1"><button onclick="browse()">Browse</button><button onclick="up()">Up</button><button onclick="mkdir()">New folder</button><label><input id="upload" type="file" hidden onchange="uploadFile()"><button onclick="upload.click()">Upload</button></label></div><div id="files" class="list"></div></section></main>
<dialog id="editor"><h2 id="editorTitle">Editor</h2><textarea id="editorText"></textarea><div class="row"><button onclick="saveText()">Save with backup</button><button onclick="editor.close()">Cancel</button></div></dialog><div id="toast"></div>
<script>let appData=[],editPath='',editHash='';const esc=s=>String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));const token=location.hash.startsWith('#token=')?decodeURIComponent(location.hash.slice(7)):localStorage.jokerToken||'';if(token)localStorage.jokerToken=token;
async function api(url,opt={}){opt.headers={...(opt.headers||{}),...(token?{'authorization':'Bearer '+token}:{})};let r=await fetch(url,opt);let kind=r.headers.get('content-type')||'';let value=kind.includes('json')?await r.json():await r.blob();if(!r.ok)throw Error(value.error||r.statusText);return value}function toastMsg(x){toast.textContent=x;toast.style.display='block';setTimeout(()=>toast.style.display='none',2200)}
async function refresh(){try{let h=await api('/api/health');connection.innerHTML='<span class="live">LIVE</span> '+esc(h.hello.fw);console.innerHTML=`<div class="metric">${h.system.temp_cpu}°C</div><div>CPU · SoC ${h.system.temp_soc}°C</div><div class="muted">PID ${h.hello.pid} · uptime ${Math.floor(h.system.uptime_s/3600)}h</div>`;pad.textContent=JSON.stringify(h.pad,null,2);storage.innerHTML=h.disk.map(d=>`<div class="item"><code>${esc(d.path)}</code><span>${(d.free/1073741824).toFixed(1)} GB free</span></div>`).join('');appData=await api('/api/apps');renderApps()}catch(e){connection.textContent='offline: '+e.message}}
function renderApps(){let q=filter.value.toLowerCase();apps.innerHTML=appData.filter(a=>(a.id+' '+a.name).toLowerCase().includes(q)).map(a=>`<div class="item"><span><code>${esc(a.id)}</code> ${esc(a.name)}</span><button onclick="launch('${encodeURIComponent(a.id)}')">Launch</button></div>`).join('')}
async function post(url,data={}){try{let j=await api(url,{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(data)});toastMsg('done');return j}catch(e){toastMsg(e.message);throw e}}
const press=b=>post('/api/pad/press',{buttons:b,ms:100}).then(refresh);const launch=id=>post('/api/apps/launch',{title_id:decodeURIComponent(id)});const notifyConsole=()=>post('/api/notify',{text:note.value});
const join=(a,b)=>(a==='/'?'':a.replace(/\/$/,''))+'/'+b;async function browse(){try{let p=path.value;let x=await api('/api/files?path='+encodeURIComponent(p));files.innerHTML=x.map(f=>{let full=join(p,f.name),q=encodeURIComponent(full);if(f.type==='d')return `<div class="item"><button onclick="path.value=decodeURIComponent('${q}');browse()">DIR ${esc(f.name)}</button><button class="danger" onclick="removePath(decodeURIComponent('${q}'))">Delete</button></div>`;return `<div class="item"><span>FILE <code>${esc(f.name)}</code> · ${f.size} bytes</span><span><button onclick="editText(decodeURIComponent('${q}'))">Edit</button> <button onclick="downloadFile(decodeURIComponent('${q}'))">Download</button> <button class="danger" onclick="removePath(decodeURIComponent('${q}'))">Delete</button></span></div>`}).join('')}catch(e){toastMsg(e.message)}}
function up(){let p=path.value.replace(/\/$/,'');path.value=p.substring(0,p.lastIndexOf('/'))||'/';browse()}async function mkdir(){let name=prompt('Folder name');if(name){await post('/api/files/mkdir',{path:join(path.value,name)});browse()}}async function removePath(p){if(confirm('Delete '+p+'?')){await post('/api/files/delete',{path:p});browse()}}
async function editText(p){try{let x=await api('/api/text?path='+encodeURIComponent(p));editPath=p;editHash=x.sha256;editorTitle.textContent=p;editorText.value=x.text;editor.showModal()}catch(e){toastMsg(e.message)}}async function saveText(){await post('/api/text',{path:editPath,text:editorText.value,expected_sha256:editHash});editor.close();browse()}
async function downloadFile(p){try{let b=await api('/api/file?path='+encodeURIComponent(p));let a=document.createElement('a');a.href=URL.createObjectURL(b);a.download=p.split('/').pop();a.click();URL.revokeObjectURL(a.href)}catch(e){toastMsg(e.message)}}async function uploadFile(){let f=upload.files[0];if(!f)return;let remote=join(path.value,f.name);try{await api('/api/file?path='+encodeURIComponent(remote),{method:'POST',headers:{'content-type':'application/octet-stream'},body:f});toastMsg('uploaded');browse()}catch(e){toastMsg(e.message)}finally{upload.value=''}}
async function captureScreen(){try{let q=new URLSearchParams({backend:shotBackend.value,request:shotRequest.value,folder:shotFolder.value});let b=await api('/api/screenshot?'+q);let old=screen.src;screen.src=URL.createObjectURL(b);screen.style.display='block';if(old.startsWith('blob:'))URL.revokeObjectURL(old);toastMsg('captured')}catch(e){toastMsg(e.message)}}refresh();browse();setInterval(refresh,15000)</script></body></html>"""


def make_handler(
    host: str,
    port: int,
    *,
    token: str | None = None,
    client_factory: Callable[[], JokerClient] | None = None,
):
    factory = client_factory or (lambda: JokerClient(host=host, port=port))

    class Handler(BaseHTTPRequestHandler):
        def _client(self) -> JokerClient:
            return factory()

        def _authorized(self) -> bool:
            if not token:
                return True
            supplied = self.headers.get("Authorization", "")
            return secrets.compare_digest(supplied, f"Bearer {token}")

        def _authorize_api(self) -> bool:
            if self._authorized():
                return True
            self._json({"error": "unauthorized"}, HTTPStatus.UNAUTHORIZED)
            return False

        def _json(self, value: object, status: HTTPStatus = HTTPStatus.OK) -> None:
            body = json.dumps(value).encode("utf-8")
            self._bytes(body, "application/json", status)

        def _bytes(self, body: bytes, content_type: str, status: HTTPStatus = HTTPStatus.OK, filename: str | None = None) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            if filename:
                self.send_header("Content-Disposition", f"attachment; filename*=UTF-8''{quote(filename)}")
            self.end_headers()
            self.wfile.write(body)

        def _raw_body(self, limit: int) -> bytes:
            length = int(self.headers.get("Content-Length", "0"))
            if length < 0 or length > limit:
                raise ValueError(f"request body exceeds {limit} bytes")
            return self.rfile.read(length)

        def _json_body(self) -> dict:
            raw = self._raw_body(MAX_TEXT_BYTES + 65536)
            value = json.loads(raw) if raw else {}
            if not isinstance(value, dict):
                raise ValueError("JSON body must be an object")
            return value

        def do_GET(self) -> None:  # noqa: N802
            route = urlparse(self.path)
            if route.path == "/":
                self._bytes(HTML.encode("utf-8"), "text/html; charset=utf-8")
                return
            if not self._authorize_api():
                return
            query = parse_qs(route.query)
            try:
                client = self._client()
                if route.path == "/api/health": self._json(AdminManager(client).health())
                elif route.path == "/api/audit": self._json(AdminManager(client).audit())
                elif route.path == "/api/apps": self._json(client.app_list())
                elif route.path == "/api/files": self._json(client.fs_ls(query.get("path", ["/data"])[0]))
                elif route.path == "/api/screenshot":
                    backend = query.get("backend", ["create"])[0]
                    if backend == "latest": shot = client.shot_latest()
                    elif backend == "grab":
                        request = query.get("request", [""])[0]
                        folder = query.get("folder", [""])[0]
                        if not request or not folder: raise ValueError("grab requires request and folder")
                        shot = client.shot_grab(request, folder)
                    elif backend == "create": shot = client.shot()
                    else: raise ValueError("backend must be create, latest, or grab")
                    self._bytes(shot.to_png(max_width=1920), "image/png")
                elif route.path in {"/api/file", "/api/text"}:
                    path = query.get("path", [""])[0]
                    data = client.fs_get(path)
                    if route.path == "/api/file": self._bytes(data, "application/octet-stream", filename=path.rsplit("/", 1)[-1])
                    else:
                        if len(data) > MAX_TEXT_BYTES: raise ValueError("file is too large for text editor")
                        self._json({"path": path, "text": data.decode("utf-8"), "sha256": hashlib.sha256(data).hexdigest()})
                else: self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)
            except Exception as exc:
                self._json({"error": str(exc)}, HTTPStatus.BAD_GATEWAY)

        def do_POST(self) -> None:  # noqa: N802
            route = urlparse(self.path)
            if not self._authorize_api():
                return
            try:
                client = self._client()
                if route.path == "/api/file":
                    path = parse_qs(route.query).get("path", [""])[0]
                    data = self._raw_body(MAX_UPLOAD_BYTES)
                    client.fs_put(path, data)
                    result = {"ok": True, "bytes": len(data)}
                else:
                    data = self._json_body()
                    if route.path == "/api/pad/open": result = client.pad_open()
                    elif route.path == "/api/pad/close": result = client.pad_close()
                    elif route.path == "/api/pad/press": client.pad_press(str(data["buttons"]), int(data.get("ms", 120))); result = {"ok": True}
                    elif route.path == "/api/apps/launch": result = client.app_launch(str(data["title_id"]))
                    elif route.path == "/api/home": client.app_home(); result = {"ok": True}
                    elif route.path == "/api/notify": client.notify(str(data["text"])); result = {"ok": True}
                    elif route.path == "/api/files/mkdir": client.fs_mkdir(str(data["path"])); result = {"ok": True}
                    elif route.path == "/api/files/delete": client.fs_rm(str(data["path"])); result = {"ok": True}
                    elif route.path == "/api/text": result = AdminManager(client).write_text(str(data["path"]), str(data["text"]), str(data["expected_sha256"]))
                    else: self._json({"error": "not found"}, HTTPStatus.NOT_FOUND); return
                self._json(result)
            except Exception as exc:
                self._json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)

        def log_message(self, fmt: str, *args: object) -> None:
            return

    return Handler


def serve(ps5_host: str, ps5_port: int, bind: str = "127.0.0.1", listen: int = 8745, token: str | None = None) -> None:
    if bind not in {"127.0.0.1", "::1", "localhost"} and not token:
        raise ValueError("--token is required when dashboard bind is not localhost")
    server = ThreadingHTTPServer((bind, listen), make_handler(ps5_host, ps5_port, token=token))
    suffix = "#token=" + quote(token) if token else ""
    print(f"Joker dashboard: http://{bind}:{listen}/{suffix}")
    server.serve_forever()
