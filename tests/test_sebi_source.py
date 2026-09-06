from ipo_watcher.sebi_source import parse_listing, FilingRow

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
