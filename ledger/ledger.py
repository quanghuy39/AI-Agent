"""So ket qua cua mot bai (vd. output/bai1). Khong import litellm.

Moi lan doc/ghi deu di thang xuong file, nen hai doi tuong Ledger cung thu muc luon thay du lieu moi nhat.
Ghi file .json an toan: ghi ra file tam cung thu muc roi os.replace de len file that.
Khong khoa file: khong dung cho nhieu tien trinh ghi cung luc.
"""

import json
import os
import shutil
import tempfile
from collections import deque
from datetime import datetime
from pathlib import Path

from ledger.schema import (
    Check,
    Decision,
    DecisionStatus,
    Finding,
    LogEvent,
    OpenItem,
    OpenItemStatus,
    ResultEntry,
    ResultStatus,
    RunState,
    now_iso,
)

PROBLEM = "problem.json"
DATA_PROFILE = "data_profile.json"
DECISIONS = "decisions.json"
RESULTS = "results.json"
FINDINGS = "findings.json"
OPEN_ITEMS = "open_items.json"
INSIGHTS = "insights.json"
REPORT_DIR = "report"
RUN_LOG = "run_log.jsonl"
STATE = "state.json"
WORK_DIR = "work"
SUPERSEDED_DIR = "_superseded"  # trong work/ va o goc ho so: file cua lan lam truoc, khong dua cho AI


def _id_number(item_id):
    return int(item_id[1:])


def _next_id(prefix, items):
    return f"{prefix}{max((_id_number(i.id) for i in items), default=0) + 1}"


class Ledger:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    # ---------- doc / ghi file ----------

    def _path(self, name):
        return self.root / name

    def _read_json(self, name, default):
        path = self._path(name)
        if not path.exists():
            return default
        with open(path, encoding="utf-8") as f:
            return json.load(f)

    def _write_json(self, name, data):
        path = self._path(name)
        fd, tmp = tempfile.mkstemp(dir=self.root, prefix=f".{name}.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
                f.write("\n")
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
        except BaseException:
            if os.path.exists(tmp):
                os.remove(tmp)
            raise

    def _read_models(self, name, model):
        return [model.model_validate(x) for x in self._read_json(name, [])]

    def _write_models(self, name, items):
        self._write_json(name, [i.model_dump(mode="json") for i in items])

    # ---------- de bai va ho so du lieu ----------

    def get_problem(self):
        return self._read_json(PROBLEM, None)

    def set_problem(self, problem):
        self._write_json(PROBLEM, problem)

    def get_data_profile(self):
        return self._read_json(DATA_PROFILE, None)

    def set_data_profile(self, profile):
        self._write_json(DATA_PROFILE, profile)

    # ---------- tong hop ----------

    def get_insights(self):
        return self._read_json(INSIGHTS, None)

    def set_insights(self, insights):
        self._write_json(INSIGHTS, insights)

    def discard_insights(self):
        """Chuyen insights.json va thu muc report/ dung tu no sang _superseded/ o goc ho so, khong xoa (ban bi tu choi,
        hoac --restart). Khong dat trong work/ de khong lan voi file cua code AI. Tra ve duong dan moi cua insights.json
        (tuong doi), khong co file thi None."""
        path = self._path(INSIGHTS)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        report = self.root / REPORT_DIR
        if report.is_dir():
            (self.root / SUPERSEDED_DIR).mkdir(exist_ok=True)
            shutil.move(report, self.root / SUPERSEDED_DIR / f"report_{stamp}")
        if not path.exists():
            return None
        target = self.root / SUPERSEDED_DIR / f"insights_{stamp}.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(path, target)
        moved = target.relative_to(self.root).as_posix()
        self.log_event("ledger", "discard_insights", extra={"moved_to": moved})
        return moved

    # ---------- tien do ----------

    def get_state(self):
        """Tien do da luu; ho so cu chua co state.json thi None."""
        data = self._read_json(STATE, None)
        return None if data is None else RunState.model_validate(data)

    def set_state(self, phase, plan=None, sensitive=None):
        state = RunState(phase=phase, plan=plan, sensitive=sensitive)
        self._write_json(STATE, state.model_dump(mode="json"))
        return state

    def update_state(self, **changes):
        """Doi mot vai truong cua state hien co (phase, plan, sensitive), cap nhat updated_at."""
        state = RunState(**{**self.get_state().model_dump(), **changes, "updated_at": now_iso()})
        self._write_json(STATE, state.model_dump(mode="json"))
        return state

    # ---------- doc danh sach ----------

    def results(self):
        return sorted(self._read_models(RESULTS, ResultEntry), key=lambda r: _id_number(r.id))

    def decisions(self):
        return sorted(self._read_models(DECISIONS, Decision), key=lambda d: _id_number(d.id))

    def active_decisions(self):
        """Decision cua lan lam hien tai, bo superseded. Moi cho dung decision (khong phai doi chieu) doc o day."""
        return [d for d in self.decisions() if d.status != DecisionStatus.SUPERSEDED]

    def findings(self):
        return sorted(self._read_models(FINDINGS, Finding), key=lambda f: _id_number(f.id))

    def open_items(self):
        return sorted(self._read_models(OPEN_ITEMS, OpenItem), key=lambda o: _id_number(o.id))

    def get_result(self, result_id):
        for r in self.results():
            if r.id == result_id:
                return r
        raise KeyError(f"không có kết quả {result_id}")

    def results_by_question(self):
        """{question_key: id} cua ket qua valid. Nguon su that duy nhat cho "cau nao da giai"."""
        return {r.question_key: r.id for r in self.results()
                if r.status == ResultStatus.VALID and r.question_key}

    def read_log(self):
        path = self._path(RUN_LOG)
        if not path.exists():
            return []
        with open(path, encoding="utf-8") as f:
            return [LogEvent.model_validate_json(line) for line in f if line.strip()]

    # ---------- ket qua ----------

    def add_result(self, question, method, result_summary, checks, depends_on=None, output_path=None,
                   code_file=None, definition_version=1, question_key=None):
        if not checks:
            raise ValueError(
                "Không ghi sổ khi thiếu kiểm chứng: checks rỗng. "
                "Nếu chưa kiểm được thì khai Check(status='unchecked') kèm lý do."
            )
        results = self.results()
        existing = {r.id for r in results}
        depends_on = list(depends_on or [])
        missing = [d for d in depends_on if d not in existing]
        if missing:
            raise ValueError(f"depends_on chứa id chưa có trong sổ: {missing}")

        entry = ResultEntry(
            id=_next_id("R", results),
            question=question,
            question_key=question_key,
            method=method,
            result_summary=result_summary,
            output_path=output_path,
            code_file=code_file,
            depends_on=depends_on,
            definition_version=definition_version,
            checks=[c if isinstance(c, Check) else Check.model_validate(c) for c in checks],
        )
        self._write_models(RESULTS, results + [entry])
        return entry

    def mark_stale(self, result_id):
        """Dat result_id thanh superseded; moi ket qua phu thuoc (truc tiep hoac gian tiep) dang valid
        thanh stale. Tra ve id bi doi thanh stale, theo thu tu id."""
        results = self.results()
        by_id = {r.id: r for r in results}
        if result_id not in by_id:
            raise KeyError(f"không có kết quả {result_id}")

        dependents = {}
        for r in results:
            for dep in r.depends_on:
                dependents.setdefault(dep, []).append(r.id)

        by_id[result_id].status = ResultStatus.SUPERSEDED
        affected = []
        seen = {result_id}
        queue = deque([result_id])
        while queue:  # BFS, seen chan vong
            for child in dependents.get(queue.popleft(), []):
                if child in seen:
                    continue
                seen.add(child)
                queue.append(child)
                if by_id[child].status == ResultStatus.VALID:
                    by_id[child].status = ResultStatus.STALE
                    affected.append(child)

        affected.sort(key=_id_number)
        self._write_models(RESULTS, results)
        self.log_event("ledger", "mark_stale", inputs={"result_id": result_id}, extra={"stale": affected})
        return affected

    def _archive_work(self):
        """Chuyen moi file trong work/ (tru _superseded/) vao work/_superseded/<thoi diem>/, khong xoa.
        Lay het thay vi theo output_path: output_path khong co file ma lan thu hong da ghi cung noi dung.
        Tra ve {"work/a.csv": duong dan moi}, tuong doi theo goc ho so."""
        work = self.root / WORK_DIR
        if not work.is_dir():
            return {}
        files = [p for p in sorted(work.rglob("*")) if p.is_file() and SUPERSEDED_DIR not in p.relative_to(work).parts]
        dest = work / SUPERSEDED_DIR / datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        moved = {}
        for path in files:
            target = dest / path.relative_to(work)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(path, target)
            moved[path.relative_to(self.root).as_posix()] = target.relative_to(self.root).as_posix()
        return moved

    def supersede_valid(self):
        """Moi ket qua valid thanh superseded va cat file work/ sang _superseded/ (chay lai ca bai).
        output_path tro toi cho moi. Tra ve cac id da doi, theo thu tu id."""
        results = self.results()
        moved = self._archive_work()
        changed = []
        for r in results:
            if r.status == ResultStatus.VALID:
                r.status = ResultStatus.SUPERSEDED
                changed.append(r.id)
                if r.output_path:
                    r.output_path = "; ".join(moved.get(p, p) for p in r.output_path.split("; "))
        self._write_models(RESULTS, results)
        self.log_event("ledger", "supersede_valid", extra={"superseded": changed, "n_files_moved": len(moved)})
        return changed

    # ---------- quyet dinh ----------

    def add_decision(self, kind, text, required=True, source_key=None):
        decisions = self.decisions()
        d = Decision(id=_next_id("D", decisions), kind=kind, text=text, required=required, source_key=source_key)
        self._write_models(DECISIONS, decisions + [d])
        return d

    def confirm_decision(self, decision_id, at=None):
        decisions = self.decisions()
        for i, d in enumerate(decisions):
            if d.id == decision_id:
                decisions[i] = d.confirm(at)
                self._write_models(DECISIONS, decisions)
                return decisions[i]
        raise KeyError(f"không có quyết định {decision_id}")

    def revise_decision(self, decision_id, text, at=None):
        """Thay noi dung bang noi dung nguoi dung go va xac nhan luon. source_key giu nguyen."""
        decisions = self.decisions()
        for i, d in enumerate(decisions):
            if d.id == decision_id:
                decisions[i] = Decision.model_validate({**d.model_dump(), "text": text}).confirm(at)
                self._write_models(DECISIONS, decisions)
                return decisions[i]
        raise KeyError(f"không có quyết định {decision_id}")

    def supersede_decisions(self, source_key=None):
        """Moi decision chua superseded (ke ca pending) thanh superseded, khong xoa. Co source_key thi chi cac decision
        cung source_key. Tra ve cac id da doi."""
        decisions = self.decisions()
        changed = []
        for d in decisions:
            if d.status != DecisionStatus.SUPERSEDED and source_key in (None, d.source_key):
                d.status = DecisionStatus.SUPERSEDED
                changed.append(d.id)
        self._write_models(DECISIONS, decisions)
        self.log_event("ledger", "supersede_decisions", extra={"superseded": changed})
        return changed

    def pending_required(self):
        return [d for d in self.decisions() if d.required and d.status == DecisionStatus.PENDING]

    # ---------- phat hien va viec mo ----------

    def add_finding(self, text, kind, result_ids=None):
        result_ids = list(result_ids or [])
        existing = {r.id for r in self.results()}
        missing = [r for r in result_ids if r not in existing]
        if missing:
            raise ValueError(f"result_ids chứa id không có trong sổ kết quả: {missing}")
        findings = self.findings()
        f = Finding(id=_next_id("F", findings), text=text, kind=kind, result_ids=result_ids)
        self._write_models(FINDINGS, findings + [f])
        return f

    def add_open_item(self, text):
        items = self.open_items()
        o = OpenItem(id=_next_id("O", items), text=text)
        self._write_models(OPEN_ITEMS, items + [o])
        return o

    def add_open_item_once(self, text):
        """Nhu add_open_item, nhung da co muc dang mo cung noi dung thi tra ve muc do."""
        for o in self.open_items():
            if o.status == OpenItemStatus.OPEN and o.text == text:
                return o
        return self.add_open_item(text)

    def close_open_items_with_text(self, text):
        for o in self.open_items():
            if o.status == OpenItemStatus.OPEN and o.text == text:
                self.close_open_item(o.id)

    def close_open_item(self, item_id):
        items = self.open_items()
        for i, o in enumerate(items):
            if o.id == item_id:
                items[i] = OpenItem.model_validate({**o.model_dump(), "status": OpenItemStatus.CLOSED})
                self._write_models(OPEN_ITEMS, items)
                return items[i]
        raise KeyError(f"không có việc mở {item_id}")

    # ---------- nhat ky ----------

    def log_event(self, step, action, inputs=None, code_file=None, checks=None, status="ok", extra=None):
        event = LogEvent(
            step=step,
            action=action,
            inputs=inputs or {},
            code_file=code_file,
            checks=[c if isinstance(c, Check) else Check.model_validate(c) for c in (checks or [])],
            status=status,
            extra=extra or {},
        )
        with open(self._path(RUN_LOG), "a", encoding="utf-8") as f:
            f.write(json.dumps(event.model_dump(mode="json"), ensure_ascii=False) + "\n")
        return event

    def attach_usage_log(self):
        """Tu gio moi luot goi AI (llm.usage) ghi vao run_log.jsonl cua bai nay, dang LogEvent."""
        from llm.usage import set_usage_log_path  # lazy: ledger khong phu thuoc llm khi import
        set_usage_log_path(self._path(RUN_LOG))

    def detach_usage_log(self):
        """Tra usage log ve mac dinh cua llm.yaml."""
        from llm.usage import set_usage_log_path
        set_usage_log_path(None)

    # ---------- tom tat va xuat ----------

    def get_summary(self, max_chars=2000):
        """Tom tat ngan cho prompt: ket qua valid, quyet dinh pending, so phat hien.
        Vuot max_chars thi bo ket qua cu nhat (id nho nhat) truoc."""
        valid = [r for r in self.results() if r.status == ResultStatus.VALID]
        result_lines = [
            f"{r.id} | {r.question} | {json.dumps(r.result_summary, ensure_ascii=False)}" for r in valid
        ]
        tail = []
        pending = [d for d in self.decisions() if d.status == DecisionStatus.PENDING]
        if pending:
            tail.append("Quyết định đang chờ:")
            tail += [f"{d.id} [{d.kind.value}] {d.text}" for d in pending]
        tail.append(f"Số phát hiện: {len(self.findings())}")

        def compose(skip):
            lines = ["Kết quả hợp lệ:"]
            if skip:
                lines.append(f"... còn {skip} kết quả nữa")
            lines += result_lines[skip:]
            return "\n".join(lines + tail)

        for skip in range(len(result_lines) + 1):
            text = compose(skip)
            if len(text) <= max_chars:
                return text
        return compose(len(result_lines))[:max_chars]  # phan quyet dinh tu no da qua dai

    def render_markdown(self, path=None):
        lines = ["# Hồ sơ phân tích", ""]

        problem = self.get_problem()
        if problem:
            lines += ["## Đề bài", ""]
            if isinstance(problem, dict):
                lines += [f"- **{k}**: {v}" for k, v in problem.items()]
            else:
                lines.append(str(problem))
            lines.append("")

        lines += ["## Quyết định", ""]
        decisions = self.active_decisions()
        if not decisions:
            lines.append("_Chưa có._")
        for d in decisions:
            state = f"đã xác nhận {d.confirmed_at}" if d.status == DecisionStatus.CONFIRMED else "đang chờ"
            need = "bắt buộc" if d.required else "không bắt buộc"
            lines.append(f"- **{d.id}** ({d.kind.value}, {need}, {state}): {d.text}")
        lines.append("")

        lines += ["## Kết quả", ""]
        results = self.results()
        if not results:
            lines.append("_Chưa có._")
        for r in results:
            lines += [f"### {r.id}: {r.question}", "", f"- Trạng thái: {r.status.value}", f"- Phương pháp: {r.method}"]
            lines.append(f"- Số liệu: `{json.dumps(r.result_summary, ensure_ascii=False)}`")
            if r.depends_on:
                lines.append(f"- Phụ thuộc: {', '.join(r.depends_on)}")
            if r.code_file:
                lines.append(f"- Code: `{r.code_file}`")
            if r.output_path:
                lines.append(f"- Kết quả: `{r.output_path}`")
            lines.append(f"- Phiên bản định nghĩa: {r.definition_version}")
            lines.append("- Kiểm chứng:")
            for c in r.checks:
                reason = f" ({c.reason})" if c.reason else ""
                lines.append(f"  - {c.name}: {c.status.value}{reason}")
            lines.append("")

        lines += ["## Phát hiện", ""]
        findings = self.findings()
        if not findings:
            lines.append("_Chưa có._")
        for f in findings:
            basis = f" (dựa trên {', '.join(f.result_ids)})" if f.result_ids else ""
            label = "bằng chứng" if f.kind.value == "evidence" else "giả thuyết"
            lines.append(f"- **{f.id}** [{label}] {f.text}{basis}")
        lines.append("")

        lines += ["## Việc còn mở", ""]
        items = self.open_items()
        if not items:
            lines.append("_Không có._")
        for o in items:
            mark = "x" if o.status == OpenItemStatus.CLOSED else " "
            lines.append(f"- [{mark}] **{o.id}** {o.text}")
        lines.append("")

        text = "\n".join(lines)
        if path is not None:
            path = Path(path)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        return text
