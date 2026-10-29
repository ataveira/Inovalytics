"""F1-08d critério 35: orçamento total de marcação medido com arquivos VOC no formato típico.

Fixture sintética (não copia anotações do conjunto PASCAL VOC): ~1 KB, 3 objetos, 84 '<'. As
medições completas estão no ADR-010; aqui só se garante que o limite vigente (600 000) comporta
o pior caso legítimo em tempo e recusa o excedente depressa.
"""

import json

from _timing import timed
from inovalytics.imports.errors import ImportRejectedError
from inovalytics.imports.voc_parser import ParsedVoc, VocLimits, parse_voc

BOX = (
    "\t<object>\n\t\t<name>{n}</name>\n\t\t<pose>Unspecified</pose>\n\t\t<truncated>0</truncated>\n"
    "\t\t<difficult>0</difficult>\n\t\t<bndbox>\n\t\t\t<xmin>10</xmin>\n\t\t\t<ymin>20</ymin>\n"
    "\t\t\t<xmax>200</xmax>\n\t\t\t<ymax>300</ymax>\n\t\t</bndbox>\n\t</object>\n"
)
TYPICAL = (
    "<annotation>\n\t<folder>VOC</folder>\n\t<filename>x.jpg</filename>\n\t<source>\n"
    "\t\t<database>d</database>\n\t\t<annotation>a</annotation>\n\t\t<image>flickr</image>\n"
    "\t</source>\n\t<size>\n\t\t<width>500</width>\n\t\t<height>375</height>\n"
    "\t\t<depth>3</depth>\n\t</size>\n\t<segmented>0</segmented>\n"
    + "".join(BOX.format(n=n) for n in ("a", "b", "a"))
    + "</annotation>\n"
)
LIM = VocLimits()


def body(count: int) -> bytes:
    return json.dumps({"annotations": {f"f{i:06d}.xml": TYPICAL for i in range(count)}}).encode()


def test_typical_fixture_shape() -> None:
    assert 900 <= len(TYPICAL.encode()) <= 1600 and TYPICAL.count("<") == 84


def test_typical_batch_at_the_budget_finishes_in_time() -> None:
    n = LIM.max_total_markup // TYPICAL.count("<")  # 7 142 arquivos
    data = body(n)
    elapsed, result = timed(lambda: parse_voc(data, LIM), 8.0)
    assert isinstance(result, ParsedVoc) and result.files_count == n and result.boxes_count == 3 * n
    assert elapsed < 4.0  # medido ~1,0 s; margem >= 2x


def test_one_file_over_the_budget_is_refused_quickly() -> None:
    data = body(LIM.max_total_markup // TYPICAL.count("<") + 1)
    elapsed, result = timed(lambda: parse_voc(data, LIM), 4.0)
    assert isinstance(result, ImportRejectedError)
    assert result.errors[0].message == "limite excedido: max_total_markup"
    assert elapsed < 1.0
