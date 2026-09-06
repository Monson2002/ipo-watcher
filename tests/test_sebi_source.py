from ipo_watcher.sebi_source import parse_listing, FilingRow
from unittest.mock import patch, MagicMock

LISTING_FIXTURE = """
<table id='sample_1'>
<thead><tr role='row'><th style='width:18%'>Date</th><th>Title</th></tr></thead>
<tbody>
<tr role='row' class='odd'>
    <td>Sep 04, 2026</td>
    <td><a href="https://www.sebi.gov.in/filings/public-issues/sep-2026/gaurik-fashions-limited-addendum-to-drhp_104276.html"  target="_blank" title="Gaurik Fashions Limited - Addendum to DRHP" class="points"> Gaurik Fashions Limited - Addendum to DRHP</a>
</tr>
<tr role='row' class='even'>
    <td>Sep 04, 2026</td>
    <td><a href="https://www.sebi.gov.in/filings/public-issues/sep-2026/india-exposition-mart-limited_104273.html"  target="_blank" title="India Exposition Mart Limited" class="points"> India Exposition Mart Limited</a>
</tr>
<tr role='row' class='odd'>
    <td>Aug 14, 2026</td>
    <td><a href="https://www.sebi.gov.in/filings/public-issues/aug-2026/tablespace-technologies-limited-drhp_103710.html"  target="_blank" title="TABLESPACE TECHNOLOGIES LIMITED - DRHP&lt;br&gt;..." class="points"> TABLESPACE TECHNOLOGIES LIMITED - DRHP<br><a href= 'https://www.sebi.gov.in/sebi_data/commondocs/aug-2026/TABLESPACE%20TECHNOLOGIES%20LIMITED%20-%20DAP_p.pdf' style=color:#000000>TABLESPACE TECHNOLOGIES LIMITED - Draft Abridged Prospectus</a></a>
</tr>
</tbody>
</table>
"""


def test_parse_listing_plain_row_with_type_suffix():
    rows = parse_listing(LISTING_FIXTURE)
    assert rows[0] == FilingRow(
        company_name="Gaurik Fashions Limited",
        filing_type="Addendum to DRHP",
        filing_date="2026-09-04",
        source_page_url="https://www.sebi.gov.in/filings/public-issues/sep-2026/gaurik-fashions-limited-addendum-to-drhp_104276.html",
    )


def test_parse_listing_row_with_no_type_suffix():
    rows = parse_listing(LISTING_FIXTURE)
    assert rows[1].company_name == "India Exposition Mart Limited"
    assert rows[1].filing_type == "Unspecified"


def test_parse_listing_row_with_nested_anchor_only_captures_outer_row():
    rows = parse_listing(LISTING_FIXTURE)
    assert len(rows) == 3
    assert rows[2].company_name == "TABLESPACE TECHNOLOGIES LIMITED"
    assert rows[2].filing_type == "DRHP"
    assert rows[2].source_page_url.endswith("tablespace-technologies-limited-drhp_103710.html")


AJAX_FRAGMENT = """<input type='hidden' name='totalpage' value=88 /><input type='hidden' name='nextDel' value='25'/><input type='hidden' name='nextValue' value='2'/>
<table id='sample_1'><tbody>
<tr role='row' class='odd'>
    <td>Aug 06, 2026</td>
    <td><a href="https://www.sebi.gov.in/filings/public-issues/aug-2026/encube-ethicals-limited-drhp_103383.html"  target="_blank" title="Encube Ethicals Limited - DRHP" class="points"> Encube Ethicals Limited - DRHP</a>
</tr>
</tbody></table>
#@#<ol class='cfc-breadcrumb'></ol>"""


def test_fetch_listing_page_2_posts_to_ajax_endpoint_and_strips_delimiter():
    from ipo_watcher import sebi_source

    fake_response = MagicMock()
    fake_response.text = AJAX_FRAGMENT
    fake_response.raise_for_status = MagicMock()

    with patch("ipo_watcher.sebi_source.requests.post", return_value=fake_response) as mock_post:
        html = sebi_source.fetch_listing_page(page_no=2)

    assert "#@#" not in html
    rows = sebi_source.parse_listing(html)
    assert rows[0].company_name == "Encube Ethicals Limited"

    _, kwargs = mock_post.call_args
    assert kwargs["data"]["nextValue"] == "1"
    assert kwargs["data"]["doDirect"] == "1"
    assert kwargs["data"]["next"] == "n"


JIO_DETAIL_FIXTURE = """
<h1>Jio Platforms Ltd - DRHP<br><a href= 'https://www.sebi.gov.in/sebi_data/commondocs/jun-2026/Jio%20Platforms%20Ltd_p.PDF' style=color:#000000>Jio Platforms Ltd - Abridged Prospectus</a></h1>
<iframe src='../../../web/?file=https://www.sebi.gov.in/sebi_data/attachdocs/jun-2026/1781870482419.PDF' width='100%' style='max-height:90%; height:600px;' allowfullscreen webkitallowfullscreen></iframe>
"""

LCC_DETAIL_FIXTURE = """
<h1>LCC PROJECTS LIMITED - RHP <br><a href= 'https://www.sebi.gov.in/sebi_data/commondocs/sep-2026/LCC%20PROJECTS%20LIMITED%20-%20AP_p.pdf' style=color:#000000>LCC PROJECTS LIMITED - Abridged Prospectus</a></h1>
<iframe src='../../../web/?file=https://www.sebi.gov.in/sebi_data/attachdocs/sep-2026/1788524729720.pdf' width='100%' style='max-height:90%; height:600px;' allowfullscreen webkitallowfullscreen></iframe>
"""


def test_parse_detail_extracts_iframe_pdf_not_abridged_prospectus():
    from ipo_watcher.sebi_source import parse_detail

    assert parse_detail(JIO_DETAIL_FIXTURE) == (
        "https://www.sebi.gov.in/sebi_data/attachdocs/jun-2026/1781870482419.PDF"
    )
    assert parse_detail(LCC_DETAIL_FIXTURE) == (
        "https://www.sebi.gov.in/sebi_data/attachdocs/sep-2026/1788524729720.pdf"
    )


def test_parse_detail_returns_none_when_no_iframe():
    from ipo_watcher.sebi_source import parse_detail

    assert parse_detail("<html><body>no viewer here</body></html>") is None
