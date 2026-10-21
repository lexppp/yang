"""Build an explicit allowlist archive: never include .env, databases or Git files."""
import hashlib
from pathlib import Path
import tarfile

root = Path(__file__).resolve().parents[1]
destination = root/'data/downloads/CoinW-server-demo.tar.gz'
destination.parent.mkdir(parents=True, exist_ok=True)
files = [root/'app.py', root/'auth.py', root/'frontend/hosted.html',
         *sorted((root/'static').glob('*')),
         *[root/'deploy'/name for name in ('README.md','install.sh','coinw.service','Caddyfile.example')]]
with tarfile.open(destination, 'w:gz') as archive:
    for path in files:
        if not path.is_file():
            raise SystemExit(f'Missing required file: {path.relative_to(root)}')
        archive.add(path, arcname='CoinW-server-demo/'+str(path.relative_to(root)))
with tarfile.open(destination) as archive:
    names = archive.getnames()
    assert all(not any(p.startswith('.') or p.endswith(('.sqlite3','.env')) for p in Path(n).parts) for n in names)
destination.with_suffix('.gz.sha256').write_text(hashlib.sha256(destination.read_bytes()).hexdigest()+'  '+destination.name+'\n')
print(f'Created {destination.name}: {len(files)} files, {destination.stat().st_size} bytes; private files excluded.')
