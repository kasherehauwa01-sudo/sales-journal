from app.services.ftp_file_utils import importable_ftp_files


def test_error_marked_html_files_are_retried():
    files = importable_ftp_files([
        "EROOR_РеестрРН_Авиаторов_22.09.2026.html",
        "EROOR_РеестрРН_Авиаторов_23.09.2026.html",
    ])
    assert files == [
        "EROOR_РеестрРН_Авиаторов_22.09.2026.html",
        "EROOR_РеестрРН_Авиаторов_23.09.2026.html",
    ]


def test_ftp_listing_accepts_paths_and_supported_extensions_only():
    assert importable_ftp_files([
        "/reports/sales.XLSX",
        r"reports\sales.htm",
        "readme.txt",
        ".",
        "..",
        "/reports/sales.XLSX",
    ]) == ["sales.XLSX", "sales.htm"]
