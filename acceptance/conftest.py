"""pytest ignores unittest's load_tests, so give it the scenario's step ORDER too.
Alphabetical order runs steps before the ones that set their shared state."""

from acceptance.test_v0 import ORDER


def pytest_collection_modifyitems(items):
    step = {name: i for i, name in enumerate(ORDER)}
    items.sort(key=lambda item: step.get(item.name, len(ORDER)))
