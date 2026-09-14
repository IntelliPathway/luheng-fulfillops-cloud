"""Create an offline, single-file demo after npm run build."""
from pathlib import Path
import base64
import re

root = Path(__file__).resolve().parents[1]
client = root / 'dist/client'
output = root / 'export'
output.mkdir(exist_ok=True)
html = (client / 'index.html').read_text()
js_path = re.search(r'<script type="module" crossorigin src="([^"]+)"', html)[1]
css_path = re.search(r'<link rel="stylesheet" crossorigin href="([^"]+)"', html)[1]
script = (client / js_path.lstrip('/')).read_text()
style = (client / css_path.lstrip('/')).read_text()
brand = 'data:image/png;base64,' + base64.b64encode((root / 'public/assets/brand.png').read_bytes()).decode()
script = script.replace('/assets/brand.png?v=2', brand).replace('</script', '<\\/script')
html = re.sub(r'<script type="module" crossorigin src="[^"]+"></script>', '', html)
html = re.sub(r'<link rel="stylesheet" crossorigin href="[^"]+">', lambda _: '<style>' + style + '</style>', html)
html = html.replace('</body>', '<script type="module">' + script + '</script></body>')
path = output / 'LuhengAI_FulfillOps_Interactive.html'
path.write_text(html)
print(path)
