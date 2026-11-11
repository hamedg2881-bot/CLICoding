import asyncio
import json

import pytest

from clicoding_orchestrator.gateway import Completion
from clicoding_orchestrator.planner import PlanningError, StructuredPlanner


class FakePlanningGateway:
    def __init__(self, content):
        self.content = content
        self.messages = None

    async def complete(self, messages):
        self.messages = messages
        return Completion(content=self.content, model="fake-planner")


class SequencePlanningGateway:
    def __init__(self, *contents):
        self.contents = list(contents)
        self.calls = []

    async def complete(self, messages):
        self.calls.append(messages)
        content = self.contents.pop(0)
        return Completion(content=content, model="fake-planner")


def valid_plan(capability="coding"):
    return {
        "goal": "Implement a small feature safely",
        "acceptance_criteria": ["The implementation is reviewed"],
        "tasks": [
            {
                "task_id": "implement",
                "objective": "Implement the requested change",
                "required_capabilities": [capability],
                "expected_output": "A patch",
            }
        ],
    }


def test_structured_planner_parses_plan_and_passes_context():
    async def scenario():
        gateway = FakePlanningGateway(
            "```json\n" + json.dumps(valid_plan()) + "\n```"
        )
        planner = StructuredPlanner(gateway, {"coding", "review"})
        plan = await planner.create_plan("Add a feature", "Project uses Python")
        assert plan.goal == "Implement a small feature safely"
        assert "Add a feature" in gateway.messages[1]["content"]
        assert "Project uses Python" in gateway.messages[1]["content"]
        assert "coding" in gateway.messages[0]["content"]

    asyncio.run(scenario())


def test_structured_planner_repairs_plan_when_required_tasks_are_missing():
    async def scenario():
        incomplete = json.dumps(
            {
                "goal": "Respond to the greeting",
                "acceptance_criteria": ["Reply in Persian"],
            }
        )
        gateway = SequencePlanningGateway(incomplete, json.dumps(valid_plan("chat")))
        planner = StructuredPlanner(gateway, {"chat", "coding"})
        plan = await planner.create_plan("سلام")

        assert plan.tasks[0].required_capabilities == ["chat"]
        assert len(gateway.calls) == 2
        repair_prompt = gateway.calls[1][-1]["content"]
        assert "non-empty tasks array" in repair_prompt
        assert "Required JSON Schema" in repair_prompt

    asyncio.run(scenario())


def test_structured_planner_rejects_unregistered_capability():
    async def scenario():
        planner = StructuredPlanner(
            FakePlanningGateway(json.dumps(valid_plan("vision"))), {"coding"}
        )
        with pytest.raises(PlanningError, match="not registered"):
            await planner.create_plan("Inspect screenshot")

    asyncio.run(scenario())


def test_structured_planner_rejects_invalid_json_after_one_repair():
    async def scenario():
        gateway = SequencePlanningGateway("not json", "still not json")
        planner = StructuredPlanner(gateway, {"coding"})
        with pytest.raises(PlanningError, match="invalid execution plan after one repair"):
            await planner.create_plan("Do work")
        assert len(gateway.calls) == 2

    asyncio.run(scenario())


def test_structured_planner_rejects_empty_request():
    planner = StructuredPlanner(FakePlanningGateway("{}"), {"coding"})
    with pytest.raises(ValueError, match="cannot be empty"):
        asyncio.run(planner.create_plan("  "))
