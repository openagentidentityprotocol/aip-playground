"""Tests for data.py — user registry and email store."""

from __future__ import annotations

import pytest

from data import USERS, authenticate_user, get_all_emails, get_emails_for_user


class TestAuthenticateUser:
    def test_valid_credentials_return_user(self):
        user = authenticate_user("alice", "alice123")
        assert user is not None
        assert user["id"] == "alice"
        assert user["email"] == "alice@example.com"
        assert user["role"] == "user"

    def test_wrong_password_returns_none(self):
        assert authenticate_user("alice", "wrong") is None

    def test_unknown_user_returns_none(self):
        assert authenticate_user("nobody", "password") is None

    def test_empty_credentials_return_none(self):
        assert authenticate_user("", "") is None

    def test_all_demo_users_authenticate(self):
        assert authenticate_user("alice", "alice123") is not None
        assert authenticate_user("bob", "bob123") is not None
        assert authenticate_user("admin", "admin123") is not None

    def test_admin_role(self):
        user = authenticate_user("admin", "admin123")
        assert user["role"] == "admin"

    def test_user_role(self):
        for username, password in [("alice", "alice123"), ("bob", "bob123")]:
            user = authenticate_user(username, password)
            assert user["role"] == "user"


class TestGetEmailsForUser:
    def test_alice_gets_two_emails(self):
        emails = get_emails_for_user("alice@example.com")
        assert len(emails) == 2

    def test_bob_gets_two_emails(self):
        emails = get_emails_for_user("bob@example.com")
        assert len(emails) == 2

    def test_admin_gets_one_email(self):
        emails = get_emails_for_user("admin@example.com")
        assert len(emails) == 1

    def test_data_isolation_alice_cannot_see_bobs_emails(self):
        alice_emails = get_emails_for_user("alice@example.com")
        addresses = {e["to"] for e in alice_emails}
        assert "bob@example.com" not in addresses

    def test_data_isolation_bob_cannot_see_alices_emails(self):
        bob_emails = get_emails_for_user("bob@example.com")
        addresses = {e["to"] for e in bob_emails}
        assert "alice@example.com" not in addresses

    def test_unknown_email_returns_empty(self):
        assert get_emails_for_user("nobody@example.com") == []

    def test_email_fields_present(self):
        emails = get_emails_for_user("alice@example.com")
        for e in emails:
            assert "id" in e
            assert "to" in e
            assert "from" in e
            assert "subject" in e
            assert "body" in e
            assert "timestamp" in e


class TestGetAllEmails:
    def test_returns_all_five_emails(self):
        assert len(get_all_emails()) == 5

    def test_includes_all_users_emails(self):
        emails = get_all_emails()
        recipients = {e["to"] for e in emails}
        assert "alice@example.com" in recipients
        assert "bob@example.com" in recipients
        assert "admin@example.com" in recipients

    def test_returns_copy_not_original(self):
        emails = get_all_emails()
        emails.clear()
        assert len(get_all_emails()) == 5
