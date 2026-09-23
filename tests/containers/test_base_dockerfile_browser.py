"""1 つ目の RUN にあるブラウザのアーキテクチャ分岐の現状固定。"""

import re
from pathlib import Path

DOCKERFILE = Path(__file__).resolve().parents[2] / "containers/base/Dockerfile"


def test_chrome_is_amd64_only_and_chromium_is_unconditional():
    lines = [line for line in DOCKERFILE.read_text().splitlines()
             if not line.lstrip().startswith("#")]
    statements = "\n".join(lines).replace("\\\n", " ")
    first = next(line for line in statements.splitlines() if line.startswith("RUN "))
    branch = re.search(r'if \[ "\$arch" = "amd64" \]; then\s+(.*?)\s+fi;', first)
    assert branch is not None
    before, body, after = first[:branch.start()], branch[1], first[branch.end():]
    assert 'arch="$(dpkg --print-architecture)";' in before
    assert 'BROWSER_PKG="";' in before
    assert 'BROWSER_PKG="google-chrome-stable";' in body
    assert "https://dl.google.com/linux/linux_signing_key.pub" in body
    assert "gpg --dearmor -o /etc/apt/keyrings/google-chrome.gpg" in body
    assert 'http://dl.google.com/linux/chrome/deb/ stable main"' in body
    assert "> /etc/apt/sources.list.d/google-chrome.list;" in body
    assert "google-chrome" not in before + after
    assert "BROWSER_PKG=" not in after
    assert not re.search(r"\b(?:if|else|elif)\b", body + after)
    install = re.search(r"apt-get install\s+([^;]+);", after)
    assert install is not None
    assert {"chromium-browser", "$BROWSER_PKG"} <= set(install[1].split())
