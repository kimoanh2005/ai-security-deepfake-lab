import os
from urllib.parse import urlparse
import requests


class OllamaService:
    def __init__(self):
        self.base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")

    def set_base_url(self, url):
        parsed = urlparse(url)
        local = parsed.hostname in ("localhost", "127.0.0.1")
        tunnel = parsed.hostname and parsed.hostname.endswith(".trycloudflare.com")
        if not ((local and parsed.scheme == "http") or (tunnel and parsed.scheme == "https")) or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ("", "/"):
            raise ValueError("Chỉ hỗ trợ HTTP localhost/127.0.0.1 hoặc HTTPS *.trycloudflare.com, không kèm đường dẫn.")
        self.base_url = url.rstrip("/")

    def check_health(self):
        try:
            response = requests.get(f"{self.base_url}/api/tags", timeout=8, allow_redirects=False)
            response.raise_for_status()
            return {"status": "connected", "url": self.base_url, "models": [m["name"] for m in response.json().get("models", [])]}
        except (requests.RequestException, ValueError, KeyError):
            return {"status": "unreachable", "url": self.base_url, "models": [], "error": "Không kết nối được Ollama. Kiểm tra Colab, tunnel và model."}

    def generate(self, prompt, model="security-bot", system=None, temperature=0, timeout=90, output_schema=None):
        try:
            body = {"model": model, "prompt": prompt, "system": system or "", "stream": False, "options": {"temperature": temperature, "seed": 42, "num_predict": 512}}
            if output_schema is not None:
                body["format"] = output_schema
            response = requests.post(f"{self.base_url}/api/generate", json=body, timeout=timeout, allow_redirects=False)
            response.raise_for_status()
            result = response.json()
            return {"success": True, "response": result.get("response", ""), "total_duration_ms": result.get("total_duration", 0) // 1_000_000}
        except (requests.RequestException, ValueError):
            return {"success": False, "error": "Ollama không phản hồi hợp lệ. Kiểm tra URL Colab và tên model; thử lại khi model đã tải xong."}


ollama_service = OllamaService()
