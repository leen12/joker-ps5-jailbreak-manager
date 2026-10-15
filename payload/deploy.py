"""Upload jokerd.elf through the PS5 Payload Manager (port 8084), restart it, and wait for hello.
Usage: python deploy.py [HOST]"""
import socket, sys, time, urllib.request

HOST = sys.argv[1] if len(sys.argv) > 1 else "192.168.4.24"
data = open(__file__.replace("deploy.py", "jokerd.elf"), "rb").read()
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
up = urllib.request.Request(f"http://{HOST}:8084/manage:upload?filename=jokerd-current.elf", data=data,
                            headers={"Content-Type": "application/octet-stream"}, method="POST")
print("upload", opener.open(up, timeout=30).status)
try:
    s = socket.create_connection((HOST, 9310), timeout=3); s.sendall(b"quit\n"); s.close()
except OSError:
    pass
for _ in range(20):
    time.sleep(0.25)
    try:
        socket.create_connection((HOST, 9310), timeout=0.3).close()
    except OSError:
        break
print("launch", opener.open(f"http://{HOST}:8084/loadpayload:/data/pldmgr/payloads/jokerd-current/jokerd-current.elf", timeout=30).status)
for _ in range(30):
    time.sleep(0.5)
    try:
        f = socket.create_connection((HOST, 9310), timeout=1).makefile("rwb")
        f.write(b"hello\n"); f.flush(); print(f.readline().decode().strip()); sys.exit(0)
    except OSError:
        pass
sys.exit("jokerd did not answer hello")
