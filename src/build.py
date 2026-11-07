"""CCAR-F 問題集 Markdown から試験 HTML と用語クイズ HTML を生成する。

使い方:
    python -I build.py <CCAR-F問題集.md のパス>
"""

import html
import json
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parent
OUT_DIR = SRC_DIR.parent
GLOSSARY_PATH = SRC_DIR / "glossary.json"
CSS_PATH = SRC_DIR / "common.css"
TEMPLATES = {
    "exam_template.html": "exam.html",
    "glossary_template.html": "glossary-quiz.html",
}
DATA_PLACEHOLDER = "/*__DATA__*/null"
CSS_PLACEHOLDER = "/*__CSS__*/"

ANSWER_SECTION_HEADING = "# 解答と解説"
OTHER_OPTIONS_PREFIX = "他の選択肢："
EXPECTED_QUESTION_COUNT = 60

DOMAIN_ROW_RE = re.compile(
    r"^\|\s*(\d)\s*\|\s*(.+?)\s*\|\s*(\d+)%\s*\|\s*(\d+)問\s*\|$"
)
SCENARIO_RE = re.compile(r"^## シナリオ(\d+)：(.+)$")
QUESTION_RE = re.compile(
    r"^### 問 (\d+\.\d+) ・(択一式|複数選択式)・(\d)つ選択・領域(\d)$"
)
OPTION_RE = re.compile(r"^([A-E])\. (.+)$")
ANSWER_RE = re.compile(r"^\*\*(\d+\.\d+) — 正解：([A-E, ]+)\*\*$")
CODE_RE = re.compile(r"`([^`]+)`")
BOLD_RE = re.compile(r"\*\*(.+?)\*\*")


@dataclass
class Question:
    """1問分のデータ。"""

    qid: str
    scenario: int
    domain: int
    pick: int
    is_multiple: bool
    stem: str = ""
    options: list[dict[str, str]] = field(default_factory=list)
    answer: list[str] = field(default_factory=list)
    rationale: str = ""
    others: str = ""


def to_html(text: str) -> str:
    """Markdown のインライン記法（`code` と **bold**）を HTML 化する。"""
    escaped = html.escape(text.strip(), quote=False)
    escaped = CODE_RE.sub(r"<code>\1</code>", escaped)
    return BOLD_RE.sub(r"<strong>\1</strong>", escaped)


def parse_domains(lines: list[str]) -> list[dict]:
    """配分表から領域の名称・配点・問題数を取り出す。"""
    domains = []
    for line in lines:
        match = DOMAIN_ROW_RE.match(line.strip())
        if match:
            number, name, weight, count = match.groups()
            domains.append({
                "id": int(number),
                "name": name,
                "weight": int(weight),
                "count": int(count),
            })
    return domains


def parse_scenarios(lines: list[str]) -> list[dict]:
    """シナリオ見出しと、その直後の説明段落を取り出す。"""
    scenarios = []
    for index, line in enumerate(lines):
        match = SCENARIO_RE.match(line)
        if not match:
            continue
        description = next(
            (text for text in lines[index + 1:] if text.strip()), ""
        )
        scenarios.append({
            "id": int(match.group(1)),
            "title": match.group(2).strip(),
            "description": to_html(description),
        })
    return scenarios


def parse_questions(lines: list[str]) -> list[Question]:
    """問題文と選択肢を取り出す。"""
    questions: list[Question] = []
    for line in lines:
        header = QUESTION_RE.match(line)
        if header:
            qid, kind, pick, domain = header.groups()
            questions.append(Question(
                qid=qid,
                scenario=int(qid.split(".")[0]),
                domain=int(domain),
                pick=int(pick),
                is_multiple=(kind == "複数選択式"),
            ))
            continue
        if not questions or not line.strip() or line.startswith("#"):
            continue
        _append_question_line(questions[-1], line)
    return questions


def _append_question_line(question: Question, line: str) -> None:
    """問題ブロック内の1行を、問題文または選択肢として追加する。"""
    if line.strip() == "---":
        return
    option = OPTION_RE.match(line)
    if option:
        question.options.append({
            "key": option.group(1),
            "text": to_html(option.group(2)),
        })
    elif not question.options:
        question.stem += to_html(line)


def attach_answers(lines: list[str], questions: list[Question]) -> None:
    """解答と解説を、対応する問題に紐づける。"""
    by_id = {question.qid: question for question in questions}
    current: Question | None = None
    for line in lines:
        header = ANSWER_RE.match(line.strip())
        if header:
            current = by_id[header.group(1)]
            current.answer = [
                key.strip() for key in header.group(2).split(",")
            ]
            continue
        text = line.strip()
        if current is None or not text or text.startswith(("#", "---")):
            continue
        if text.startswith(OTHER_OPTIONS_PREFIX):
            current.others = to_html(text[len(OTHER_OPTIONS_PREFIX):])
        else:
            current.rationale += to_html(text)


def validate(questions: list[Question]) -> None:
    """解析漏れがあれば例外で止める（不完全な HTML を出さないため）。"""
    if len(questions) != EXPECTED_QUESTION_COUNT:
        raise ValueError(f"問題数が {len(questions)} 件です")
    for question in questions:
        problems = []
        if not question.stem:
            problems.append("問題文なし")
        if len(question.answer) != question.pick:
            problems.append("正解数が選択数と不一致")
        if not question.rationale:
            problems.append("解説なし")
        keys = {option["key"] for option in question.options}
        if not set(question.answer) <= keys:
            problems.append("正解が選択肢にない")
        if problems:
            raise ValueError(f"問 {question.qid}: {', '.join(problems)}")


def load_glossary() -> list[dict]:
    """用語集を読み込み、表示用に HTML エスケープする。"""
    entries = json.loads(GLOSSARY_PATH.read_text(encoding="utf-8"))
    text_keys = ("term", "short", "explain", "analogy")
    for entry in entries:
        for key in text_keys:
            entry[key] = to_html(entry[key])
    return entries


def render(template_name: str, data: dict, css: str) -> str:
    """テンプレートにデータと CSS を埋め込む。"""
    template = (SRC_DIR / template_name).read_text(encoding="utf-8")
    # </script> で script 要素が途中終了しないようにする
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    return (
        template.replace(DATA_PLACEHOLDER, payload)
        .replace(CSS_PLACEHOLDER, css)
    )


def main(markdown_path: Path) -> None:
    """Markdown を解析して2つの HTML を出力する。"""
    lines = markdown_path.read_text(encoding="utf-8").splitlines()
    split_at = lines.index(ANSWER_SECTION_HEADING)
    question_lines, answer_lines = lines[:split_at], lines[split_at:]

    questions = parse_questions(question_lines)
    attach_answers(answer_lines, questions)
    validate(questions)

    data = {
        "domains": parse_domains(question_lines),
        "scenarios": parse_scenarios(question_lines),
        "questions": [asdict(question) for question in questions],
        "glossary": load_glossary(),
    }
    css = CSS_PATH.read_text(encoding="utf-8")
    for template_name, output_name in TEMPLATES.items():
        output = OUT_DIR / output_name
        output.write_text(render(template_name, data, css), encoding="utf-8")
        print(f"生成: {output}")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
