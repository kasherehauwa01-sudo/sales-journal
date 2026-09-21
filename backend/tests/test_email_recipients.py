import pytest
from app.services.email_recipients import parse_recipient_emails

def test_multiple_recipients_are_read_from_separate_lines():
 assert parse_recipient_emails("one@example.ru\n\ntwo@example.ru\nONE@example.ru")==["one@example.ru","two@example.ru"]

def test_invalid_recipient_is_rejected():
 with pytest.raises(ValueError,match="Некорректный email"):
  parse_recipient_emails("not-an-email")

def test_at_least_one_recipient_is_required():
 with pytest.raises(ValueError,match="хотя бы один"):
  parse_recipient_emails("\n  \n")
