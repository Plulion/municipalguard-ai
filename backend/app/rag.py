from io import BytesIO
import re
from pypdf import PdfReader
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
from sqlalchemy.orm import Session
from .models import DocumentChunk

SPANISH_STOP_WORDS = [
    "a", "al", "algo", "algunas", "algunos", "ante", "antes", "como", "con", "contra",
    "cual", "cuando", "de", "del", "desde", "donde", "durante", "e", "el", "ella",
    "ellas", "ellos", "en", "entre", "era", "eran", "es", "esa", "esas", "ese", "eso",
    "esos", "esta", "estaba", "estado", "estan", "estas", "este", "esto", "estos",
    "fue", "fueron", "ha", "han", "hasta", "hay", "la", "las", "le", "les", "lo",
    "los", "mas", "me", "mi", "mis", "mucho", "muy", "ni", "no", "nos", "o", "otra",
    "otras", "otro", "otros", "para", "pero", "por", "porque", "que", "quien", "se",
    "segun", "ser", "si", "sin", "sobre", "son", "su", "sus", "tambien", "te", "tiene",
    "todo", "todos", "un", "una", "unas", "uno", "unos", "y", "ya",
]


def extract_pages(filename: str, raw: bytes) -> list[tuple[int | None, str]]:
    if filename.lower().endswith(".pdf"):
        reader = PdfReader(BytesIO(raw))
        return [(i + 1, page.extract_text() or "") for i, page in enumerate(reader.pages)]
    return [(None, raw.decode("utf-8", errors="replace"))]


def split_chunks(text: str, size: int = 900, overlap: int = 120) -> list[str]:
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return []
    chunks: list[str] = []
    start = 0
    length = len(text)
    while start < length:
        end = min(length, start + size)
        if end < length:
            boundary = text.rfind(" ", start, end)
            if boundary > start + size // 2:
                end = boundary
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= length:
            break
        next_start = max(start + 1, end - overlap)
        if next_start > 0 and next_start < length and text[next_start - 1] != " ":
            space_after = text.find(" ", next_start, end)
            if space_after != -1:
                next_start = space_after + 1
        start = next_start
    return chunks


def index_document(db: Session, tenant_id: str, filename: str, pages: list[tuple[int | None, str]]) -> int:
    total = 0
    for page, text in pages:
        for i, chunk in enumerate(split_chunks(text)):
            db.add(
                DocumentChunk(
                    tenant_id=tenant_id,
                    filename=filename,
                    page_number=page,
                    chunk_index=i,
                    content=chunk,
                )
            )
            total += 1
    db.commit()
    return total


def retrieve(db: Session, tenant_id: str, question: str, top_k: int = 5):
    docs = db.query(DocumentChunk).filter_by(tenant_id=tenant_id).all()
    if not docs or not question.strip():
        return []
    corpus = [d.content for d in docs]
    vectorizer = TfidfVectorizer(
        ngram_range=(1, 2),
        strip_accents="unicode",
        stop_words=SPANISH_STOP_WORDS,
    )
    try:
        corpus_matrix = vectorizer.fit_transform(corpus)
    except ValueError:
        return []
    query_vec = vectorizer.transform([question])
    scores = cosine_similarity(query_vec, corpus_matrix).ravel()
    order = scores.argsort()[::-1][:top_k]
    return [
        {
            "source": docs[i].filename,
            "page": docs[i].page_number,
            "chunk": docs[i].chunk_index,
            "score": round(float(scores[i]), 4),
            "excerpt": docs[i].content,
        }
        for i in order
        if scores[i] > 0
    ]
