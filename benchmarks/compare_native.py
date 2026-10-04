#!/usr/bin/env python3
"""Complete production apps: same synthetic Herdr socket, exact logs and children."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import socketserver
import statistics
import sys
import tempfile
import threading
import time
from compare_rust import ROOT, terminal_run, stats, close_enough, transcript


def fixtures(root, count=6):
    claude=root/"claude/projects/synthetic";codex=root/"codex/sessions/synthetic"
    claude.mkdir(parents=True);codex.mkdir(parents=True)
    log=transcript(root, messages=4000)
    agents=[]
    for i in range(count):
        sid=f"00000000-0000-0000-0000-{i:012d}"
        provider="claude" if i%2==0 else "codex"
        path=(claude if provider=="claude" else codex)/(sid+".jsonl")
        if provider=="claude":
            shutil.copyfile(log,path)
            children=path.with_suffix("")/"subagents";children.mkdir(parents=True)
            for n in range(3):
                (children/f"agent-{n}.meta.json").write_text(json.dumps({"name":f"Synthetic child {n}"}))
                (children/f"agent-{n}.jsonl").write_text(json.dumps({"type":"assistant","isSidechain":True,"timestamp":"2026-10-04T10:00:00Z","effort":"high","message":{"id":f"m{n}","model":"claude-opus-5-5","content":[],"usage":{"input_tokens":1000,"output_tokens":100}}})+"\n")
        else:
            records=[{"type":"session_meta","timestamp":"2026-10-04T10:00:00Z","payload":{"id":sid}},
                     {"type":"turn_context","payload":{"model":"gpt-6.1-sol","effort":"high"}}]
            for n in range(4000):
                records.append({"type":"token_usage_record","payload":{"thread_token_usage":{"input_tokens":(n+1)*1000,"output_tokens":(n+1)*100,"total_tokens":(n+1)*1100},"usage":{"input_tokens":1000,"output_tokens":100}}})
            records.append({"type":"response_item","payload":{"type":"function_call","name":"exec_command","call_id":"latest","arguments":"{}"}})
            path.write_text("".join(json.dumps(r)+"\n" for r in records))
            for n in range(3):
                (codex/f"child-{i}-{n}.jsonl").write_text(json.dumps({"type":"session_meta","payload":{"id":f"child-{i}-{n}","parent_thread_id":sid,"agent_nickname":f"Synthetic child {n}"}})+"\n")
        agents.append(dict(pane_id=f"p{i:04}",agent=provider,name=f"worker{i}",title="Synthetic grid task",agent_status="working",agent_session={"kind":"id","value":sid}))
    return {"agents":agents}


class Server(socketserver.ThreadingUnixStreamServer):
    daemon_threads=True
class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        try:
            request=json.loads(self.rfile.readline(65536));method=request["method"]
            if method=="session.snapshot":result={"snapshot":self.server.snapshot}
            elif method=="pane.read":result={"read":{"text":"● Bash(synthetic check)"}}
            else:raise AssertionError("benchmark must not focus or mutate agents")
            self.wfile.write(json.dumps({"result":result}).encode()+b"\n")
        except (BrokenPipeError,ConnectionResetError):pass


def main():
    p=argparse.ArgumentParser();p.add_argument("--rounds",type=int,default=7);p.add_argument("--seconds",type=float,default=4);p.add_argument("--output",type=Path,default=ROOT/"benchmarks/native-release.json");args=p.parse_args()
    keys=[b"\x1bOC",b"\x1bOD",b"\t",b"z",b"z",b"\x1bOD"]*10+[b"/",b"g",b"r",b"i",b"d",b"\x7f",b"\x7f",b"\x7f",b"\x7f",b"\r"]
    result=dict(synthetic=True,python=platform.python_version(),platform=platform.platform(),rounds=args.rounds,seconds=args.seconds,terminal=[140,38],scenarios={},sources={})
    paths=[*sorted((ROOT/"native").glob("*.rs")),*sorted((ROOT/"src/herdr_agent_grid").glob("*.py")),*sorted((ROOT/"assets").glob("*.json")),Path(__file__),ROOT/"benchmarks/native_live_worker.py",ROOT/"Cargo.lock"]
    result["sources"]={str(path.relative_to(ROOT)):hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
    with tempfile.TemporaryDirectory(prefix="grid-native-",dir="/tmp") as folder:
        root=Path(folder);snapshot=fixtures(root);sock=root/"herdr.sock"
        result["fixtures"]={str(path.relative_to(root)):hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(root.rglob("*.jsonl"))}
        server=Server(str(sock),Handler);server.snapshot=snapshot
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        common=["env","HERDR_ENV=1",f"HERDR_SOCKET_PATH={sock}",f"HOME={root}",f"CLAUDE_CONFIG_DIR={root/'claude'}",f"CODEX_HOME={root/'codex'}","HERDR_AGENT_GRID_ICONS=unicode","HERDR_AGENT_GRID_TRACE=1","HERDR_AGENT_GRID_CLAUDE_DIRS=","HERDR_GRID_CLAUDE_DIRS="]
        try:
            for scenario in ("live_active_6","live_settled_6","live_reduced_motion_6"):
                for a in snapshot["agents"]:a["agent_status"]="done" if scenario=="live_settled_6" else "working"
                pairs=[]
                print(scenario,flush=True)
                for r in range(args.rounds):
                    pair={}
                    for runtime in (("python","rust") if r%2==0 else ("rust","python")):
                        cmd=[*common,"HERDR_AGENT_GRID_MOTION="+("off" if scenario=="live_reduced_motion_6" else "on")]
                        cmd += [sys.executable,str(ROOT/"benchmarks/native_live_worker.py")] if runtime=="python" else [str(ROOT/"target/release/herdr-agent-grid")]
                        pair[runtime]=terminal_run(cmd,args.seconds,keys)
                    close_enough(pair["python"]["key_states"],pair["rust"]["key_states"]);pairs.append(pair)
                    print(f"  pair {r+1}/{args.rounds}",flush=True)
                summary={"raw_pairs":pairs}
                for runtime in ("python","rust"):
                    summary[runtime]={k:stats([p[runtime][k] for p in pairs]) for k in ("first_frame_ms","ready_frame_ms","one_core_cpu_percent","peak_rss_bytes","terminal_bytes_per_second")}
                    summary[runtime]["key_to_paint_ms"]=stats([v for p in pairs for v in p[runtime]["raw_key_ms"]])
                result["scenarios"][scenario]=summary;args.output.write_text(json.dumps(result,indent=2)+"\n")
        finally:server.shutdown();server.server_close();thread.join()
    print(args.output)

if __name__=="__main__":main()
