"""Owner-only metadata check; never downloads or loads model weights."""
import json
from src.model_identity import verify_identity, LocalModelError

if __name__ == '__main__':
    try:
        print(json.dumps(verify_identity()))
    except LocalModelError as exc:
        print(str(exc))
        raise SystemExit(1)
