"""Round-trip of a cycling dataset through a wiki file distribution.

The live counterpart of ``test_dataset_distribution.py``: the rows are uploaded
to a real wiki as a ``WikiFile``, referenced from a ``Distribution``, and read
back through the same controller methods the dashboard uses.

Writes to the wiki, so it only runs when ``TEST_OSL_DOMAIN`` is set::

    TEST_OSL_DOMAIN=wiki-dev.open-semantic-lab.org \\
    TEST_OSL_CRED_FILEPATH=../../scripts/accounts.pwd.yaml \\
    pytest tests/test_dataset_distribution_wiki.py

It self-skips when the credentials or the connection are unavailable, and
deletes the uploaded file again.
"""

import os
import uuid

import pytest

from opensemantic.batteries import CyclingDataRow, CyclingDatasetController
from opensemantic.core.v1 import Label

pytest.importorskip("osw")

DOMAIN = os.environ.get("TEST_OSL_DOMAIN")
CRED_FILEPATH = os.environ.get("TEST_OSL_CRED_FILEPATH")

pytestmark = pytest.mark.skipif(
    not DOMAIN, reason="TEST_OSL_DOMAIN not set"
)


@pytest.fixture(scope="module")
def osw_obj():
    from osw.express import OswExpress

    try:
        return OswExpress(domain=DOMAIN, cred_filepath=CRED_FILEPATH)
    except Exception as error:  # noqa: BLE001 - no wiki, no test
        pytest.skip(f"cannot connect to {DOMAIN}: {error}")


@pytest.fixture
def dataset():
    return CyclingDatasetController(
        label=[Label(text=f"cycling test {uuid.uuid4().hex[:8]}")],
        data=[
            CyclingDataRow(
                test_time={"value": float(i)},
                voltage={"value": 3.0 + i * 0.1},
                current={"value": 0.5},
            )
            for i in range(5)
        ],
    )


def test_rows_round_trip_through_a_wiki_file(osw_obj, dataset):
    from osw.controller.file.wiki import WikiFileController

    expected = dataset.to_df()["voltage"].pint.magnitude.tolist()

    wiki_file = WikiFileController(
        label=[Label(text=f"{uuid.uuid4().hex}.json")], osw=osw_obj
    )
    distribution = dataset.externalize_data(wiki_file, fmt="json")
    try:
        assert dataset.data is None
        assert distribution.media_type == "application/json"

        rows = dataset.materialize_data()

        assert len(rows) == 5
        restored = dataset.to_df()["voltage"].pint.magnitude.tolist()
        assert restored == pytest.approx(expected)
        assert dataset.data[1].voltage.unit.name == "volt"
    finally:
        try:
            wiki_file.delete()
        except Exception:  # noqa: BLE001 - leave cleanup failures silent
            pass
