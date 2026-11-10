"""CCAR-F 問題集 Markdown から試験 HTML と用語クイズ HTML を生成する。

使い方:
    python -I build.py <CCAR-F問題集.md> <CCAR-F対策問題集.md>
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
EXAM_TEMPLATE = "exam_template.html"
GLOSSARY_TEMPLATE = "glossary_template.html"
GLOSSARY_OUTPUT = "glossary-quiz.html"
DATA_PLACEHOLDER = "/*__DATA__*/null"
CSS_PLACEHOLDER = "/*__CSS__*/"

ANSWER_SECTION_HEADING = "# 解答と解説"
OTHER_OPTIONS_PREFIX = "他の選択肢："

DOMAIN_ROW_RE = re.compile(
    r"^\|\s*(\d)\s*\|\s*(.+?)\s*\|\s*(\d+)%\s*\|\s*(\d+)問\s*\|$"
)
OBJECTIVE_ROW_RE = re.compile(
    r"^\|\s*(W\d+)\s*\|\s*(\d+)%\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|$"
)
SCENARIO_RE = re.compile(r"^## シナリオ(\d+)：(.+)$")
QUESTION_RE = re.compile(
    r"^### 問 (\d+\.\d+) ・(択一式|複数選択式)・(\d)つ選択・領域(\d)"
    r"(?:・目標(W\d+))?(?:・(標準|難))?$"
)
OPTION_RE = re.compile(r"^([A-E])\. (.+)$")
ANSWER_RE = re.compile(r"^\*\*(\d+\.\d+) — 正解：([A-E, ]+)\*\*$")
CODE_RE = re.compile(r"`([^`]+)`")
BOLD_RE = re.compile(r"\*\*(.+?)\*\*")


@dataclass(frozen=True)
class QuestionSet:
    """出力する問題集1つ分の設定。"""

    output: str
    storage_key: str
    title: str
    subtitle: str
    ref_field: str


QUESTION_SETS = (
    QuestionSet(
        output="exam.html",
        storage_key="ccarf",
        title="CCAR-F 模擬試験",
        subtitle="Claude Certified Architect – Foundations",
        ref_field="qrefs",
    ),
    QuestionSet(
        output="remedial.html",
        storage_key="ccarf.remedial",
        title="CCAR-F 弱点対策問題集",
        subtitle="スコアレポート（2026-11-09・627点）の弱点目標に特化・難しめ",
        ref_field="rrefs",
    ),
)


@dataclass
class Question:
    """1問分のデータ。"""

    qid: str
    scenario: int
    domain: int
    pick: int
    is_multiple: bool
    objective: str = ""
    difficulty: str = ""
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


def parse_objectives(lines: list[str]) -> list[dict]:
    """試験目標の表（ID・本番の正答率・目標・決め手）を取り出す。"""
    objectives = []
    for line in lines:
        match = OBJECTIVE_ROW_RE.match(line.strip())
        if match:
            objective_id, score, text, tip = match.groups()
            objectives.append({
                "id": objective_id,
                "score": int(score),
                "text": to_html(text),
                "tip": to_html(tip),
            })
    return objectives


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
            qid, kind, pick, domain, objective, difficulty = header.groups()
            questions.append(Question(
                qid=qid,
                scenario=int(qid.split(".")[0]),
                domain=int(domain),
                pick=int(pick),
                is_multiple=(kind == "複数選択式"),
                objective=objective or "",
                difficulty=difficulty or "",
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
        if current is None or not text or text.startswith(("#", "---", "*")):
            continue
        if text.startswith(OTHER_OPTIONS_PREFIX):
            current.others = to_html(text[len(OTHER_OPTIONS_PREFIX):])
        else:
            current.rationale += to_html(text)


def validate(
    questions: list[Question], expected: int, objective_ids: set[str]
) -> None:
    """解析漏れがあれば例外で止める（不完全な HTML を出さないため）。"""
    if len(questions) != expected:
        raise ValueError(f"問題数が {len(questions)} 件（期待 {expected}）")
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
        if objective_ids and question.objective not in objective_ids:
            problems.append("試験目標が未定義")
        if problems:
            raise ValueError(f"問 {question.qid}: {', '.join(problems)}")


def parse_question_set(markdown_path: Path) -> dict:
    """問題集 Markdown 1冊分を解析して検証済みのデータを返す。"""
    lines = markdown_path.read_text(encoding="utf-8").splitlines()
    split_at = lines.index(ANSWER_SECTION_HEADING)
    question_lines, answer_lines = lines[:split_at], lines[split_at:]

    domains = parse_domains(question_lines)
    objectives = parse_objectives(question_lines)
    questions = parse_questions(question_lines)
    attach_answers(answer_lines, questions)
    validate(
        questions,
        sum(domain["count"] for domain in domains),
        {objective["id"] for objective in objectives},
    )
    return {
        "domains": domains,
        "objectives": objectives,
        "scenarios": parse_scenarios(question_lines),
        "questions": [asdict(question) for question in questions],
    }


def load_glossary() -> list[dict]:
    """用語集を読み込み、表示用に HTML エスケープする。"""
    entries = json.loads(GLOSSARY_PATH.read_text(encoding="utf-8"))
    text_keys = ("term", "short", "explain", "analogy")
    for entry in entries:
        for key in text_keys:
            entry[key] = to_html(entry[key])
        entry.setdefault("qrefs", [])
        entry.setdefault("rrefs", [])
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


def write_output(name: str, content: str) -> None:
    """生成した HTML を出力ディレクトリに書き出す。"""
    output = OUT_DIR / name
    output.write_text(content, encoding="utf-8")
    print(f"生成: {output}")


def main(markdown_paths: list[Path]) -> None:
    """各問題集と用語集から HTML を出力する。"""
    if len(markdown_paths) != len(QUESTION_SETS):
        raise SystemExit(__doc__)
    css = CSS_PATH.read_text(encoding="utf-8")
    glossary = load_glossary()
    first_set_domains: list[dict] = []
    for config, path in zip(QUESTION_SETS, markdown_paths):
        data = parse_question_set(path)
        data["glossary"] = glossary
        data["meta"] = asdict(config)
        first_set_domains = first_set_domains or data["domains"]
        write_output(config.output, render(EXAM_TEMPLATE, data, css))
    glossary_data = {"domains": first_set_domains, "glossary": glossary}
    write_output(
        GLOSSARY_OUTPUT, render(GLOSSARY_TEMPLATE, glossary_data, css)
    )


if __name__ == "__main__":
    main([Path(arg) for arg in sys.argv[1:]])
