"""Тимчасовий скрипт: витягнути текст із .docx у .txt для читання специфікації."""
import re
import zipfile
from pathlib import Path

FOLDER = Path(r"c:\Users\Laptop\Desktop\Нова папка\для проекту")
OUT = Path(r"c:\Users\Laptop\Desktop\Нова папка\multishop\_out")
OUT.mkdir(exist_ok=True)


def dump(path: Path) -> str:
    with zipfile.ZipFile(path) as z:
        xml = z.read("word/document.xml").decode("utf-8")
    xml = xml.replace("</w:p>", "\n").replace("</w:tr>", "\n").replace("</w:tc>", "\t")
    text = re.sub(r"<[^>]+>", "", xml)
    text = text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    lines = [ln.rstrip() for ln in text.splitlines()]
    return "\n".join(ln for ln in lines if ln.strip())


for p in sorted(FOLDER.glob("*.docx")):
    (OUT / (p.stem + ".txt")).write_text(dump(p), encoding="utf-8")
    print("ok:", p.name)
