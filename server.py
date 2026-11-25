import os
from waitress import serve
from app import app, get_lan_ip

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5001))
    print(f"\n  SuperChess server running on port {port}")
    print(f"  Same network: http://{get_lan_ip()}:{port}\n")
    serve(app, host="0.0.0.0", port=port, threads=16)