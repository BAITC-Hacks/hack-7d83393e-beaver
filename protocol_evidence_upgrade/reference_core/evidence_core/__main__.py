import argparse
import json
from pathlib import Path
from .audit import audit
from .ledger import replay

def main():
    parser = argparse.ArgumentParser(description='Reference checks, not an AI meeting processor')
    sub = parser.add_subparsers(dest='command', required=True)
    verify = sub.add_parser('verify')
    verify.add_argument('file', type=Path)
    view = sub.add_parser('replay')
    view.add_argument('file', type=Path)
    view.add_argument('--at-ms', type=int)
    serve = sub.add_parser('serve')
    serve.add_argument('--port', type=int, default=8765)
    args = parser.parse_args()
    if args.command == 'serve':
        import uvicorn
        uvicorn.run('evidence_core.app:app', host='127.0.0.1', port=args.port)
        return
    try:
        raw = json.loads(args.file.read_text(encoding='utf-8'))
        result = audit(raw) if args.command == 'verify' else replay(raw, args.at_ms)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        raise SystemExit(1 if result.get('status') == 'FAIL' else 0)
    except (OSError, ValueError) as exc:
        print(json.dumps({'status':'FAIL','reason':type(exc).__name__}))
        raise SystemExit(2)

if __name__ == '__main__':
    main()
