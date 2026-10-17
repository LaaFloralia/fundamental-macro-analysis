"""The USOIL analysis stages plugged into the generic workflow (usoil.stages.Stages)."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
import sys

from usoil import analysis, facts as facts_mod, report, scoring
from usoil.common import JobError, parse_time
from usoil.jsonschema_lite import validate as schema_validate
from usoil.stages import ReportParts, RunContext

MACHINE_SCHEMA = Path(__file__).resolve().parent / 'schemas' / 'machine.schema.json'
FACTS_SCHEMA = Path(__file__).resolve().parent / 'schemas' / 'facts.schema.json'


def last_success(ctx: RunContext) -> datetime | None:
    """Collection start of this job's latest parent-passed edition of the same mode (news lookback, design 9)."""
    from usoil.workflow import Job, previous_passed_editions
    found = previous_passed_editions(Job(root=ctx.root, python=Path(sys.executable)), ctx.mode, limit=1,
                                     before=ctx.started_at)
    return parse_time(found[0]['collected_at']) if found else None


def _schema(path: Path) -> dict:
    import json
    return json.loads(path.read_text(encoding='utf-8'))


class UsoilStages:
    def collect_context(self, ctx: RunContext) -> dict:
        from usoil.collect import collect_context
        return collect_context(ctx.mode, started_at=ctx.started_at, last_success=last_success(ctx))

    def finish_collection(self, context: dict, scores: dict | None, selection: dict, ctx: RunContext) -> dict:
        from usoil.collect import finish_collection
        return finish_collection(context, scores, selection)

    def build_facts(self, collection: dict, ctx: RunContext) -> dict:
        built = facts_mod.build(collection, mode=ctx.mode, session_slot=ctx.session_slot,
                                collection_path=ctx.work_dir / 'collection.json', history_dir=ctx.history_dir)
        errors = schema_validate(built, _schema(FACTS_SCHEMA))
        if errors:
            raise JobError('facts_invalid', 'Facts violate usoil/schemas/facts.schema.json: ' + '; '.join(errors[:5]))
        return built

    def analysis_input(self, collection: dict, facts: dict, ctx: RunContext) -> str:
        preview = scoring.evaluate(facts, mode=ctx.mode, assessments=None)
        return analysis.briefing(facts, preview, edition_id=ctx.work_dir.name, mode=ctx.mode,
                                 session_slot=ctx.session_slot, previous=ctx.previous_editions)

    def analysis_schema(self) -> dict:
        return analysis.schema()

    def validate_analysis(self, parent: dict, facts: dict, ctx: RunContext) -> dict:
        return analysis.validate(parent, facts, edition_id=ctx.work_dir.name)

    def build_report(self, validated: dict, facts: dict, collection: dict, ctx: RunContext) -> ReportParts:
        parts = report.build(validated, facts, collection, ctx)
        from usoil import d1_bias
        d1 = d1_bias.request_reference(ctx.work_dir)  # stored by the entry; None without D1 (fail-open)
        if d1:
            parts.machine_extensions['d1_bias'] = d1
        return parts

    def after_render(self, root: Path, machine: dict, run: str, at: str) -> None:
        """Local scoring log for later calibration: D1 label/probabilities next to the final label."""
        from usoil import d1_bias
        d1_bias.record_scores(root, machine.get('d1_bias'), machine, run=run, at=at)

    def validate_machine(self, machine: dict, parts: ReportParts) -> None:
        """machine.json schema (own validator, unknown keywords are errors) and MD decision block consistency."""
        errors = schema_validate(machine, _schema(MACHINE_SCHEMA))
        if errors:
            raise JobError('machine_invalid', 'machine.json violates usoil/schemas/machine.schema.json: '
                           + '; '.join(errors[:5]))
        try:
            report.check_markdown(parts.markdown, machine)
        except report.ReportError as error:
            raise JobError('markdown_machine_mismatch', str(error)) from None
