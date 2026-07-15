"""Unit tests for cli_output module."""

from unittest.mock import Mock, patch

from cli_output.status import (
    _create_progress_bar,
    _display_bucket_credit_line,
    _display_credit_line,
    _display_status_message,
    print_status,
)


class TestProgressBar:
    """Tests for _create_progress_bar function."""

    def test_empty_progress_bar(self):
        """Test progress bar with 0% completion."""
        bar = _create_progress_bar(0, 100, width=30)
        assert bar == "░" * 30
        assert len(bar) == 30

    def test_full_progress_bar(self):
        """Test progress bar with 100% completion."""
        bar = _create_progress_bar(100, 100, width=30)
        assert bar == "█" * 30
        assert len(bar) == 30

    def test_half_progress_bar(self):
        """Test progress bar with 50% completion."""
        bar = _create_progress_bar(50, 100, width=30)
        assert bar == "█" * 15 + "░" * 15
        assert len(bar) == 30

    def test_small_progress(self):
        """Test progress bar with small percentage."""
        bar = _create_progress_bar(4, 50, width=30)
        assert bar.startswith("██")
        assert bar.endswith("░")
        assert len(bar) == 30

    def test_zero_total(self):
        """Test progress bar with zero total (edge case)."""
        bar = _create_progress_bar(0, 0, width=30)
        assert bar == "░" * 30
        assert len(bar) == 30

    def test_custom_width(self):
        """Test progress bar with custom width."""
        bar = _create_progress_bar(5, 10, width=10)
        assert len(bar) == 10
        assert bar == "█" * 5 + "░" * 5


class TestDisplayCreditLine:
    """Tests for _display_credit_line function."""

    @patch("cli_output.status.console")
    def test_display_active_free_trial(self, mock_console):
        """Test displaying active free trial credits."""
        plan_credits = {
            "type": "free",
            "total": 50,
            "remaining": 4,
            "period_end": "2028-12-01T00:00:00+00:00",
        }
        _display_credit_line(plan_credits)

        mock_console.print.assert_called_once()
        call_args = mock_console.print.call_args[0][0]
        assert "Free trial" in call_args
        assert "4 of 50 remaining" in call_args
        assert "expires Dec 1, 2028" in call_args

    @patch("cli_output.status.console")
    def test_display_active_pro_plan(self, mock_console):
        """Test displaying active PRO plan credits."""
        plan_credits = {
            "type": "pro",
            "total": 1000,
            "remaining": 200,
            "period_end": "2028-07-15T00:00:00+00:00",
        }
        _display_credit_line(plan_credits)

        call_args = mock_console.print.call_args[0][0]
        assert "PRO plan" in call_args
        assert "200 of 1000 remaining" in call_args
        assert "expires Jul 15, 2028" in call_args

    @patch("cli_output.status.console")
    def test_display_expired_credits(self, mock_console):
        """Test displaying expired credits."""
        plan_credits = {
            "type": "free",
            "total": 50,
            "remaining": 4,
            "period_end": "2024-05-01T00:00:00+00:00",
        }
        _display_credit_line(plan_credits)

        call_args = mock_console.print.call_args[0][0]
        assert "expired May 1, 2024" in call_args
        assert "remaining" not in call_args

    @patch("cli_output.status.console")
    def test_display_exhausted_credits(self, mock_console):
        """Test displaying exhausted but not expired credits."""
        plan_credits = {
            "type": "free",
            "total": 50,
            "remaining": 0,
            "period_end": "2028-12-01T00:00:00+00:00",
        }
        _display_credit_line(plan_credits)

        call_args = mock_console.print.call_args[0][0]
        assert "0 of 50 remaining" in call_args
        assert "expires Dec 1, 2028" in call_args

    @patch("cli_output.status.console")
    def test_timezone_naive_datetime(self, mock_console):
        """Test handling of timezone-naive datetime."""
        plan_credits = {
            "type": "free",
            "total": 50,
            "remaining": 4,
            "period_end": "2026-12-31T23:59:59",  # No timezone
        }
        # Should not raise exception
        _display_credit_line(plan_credits)
        mock_console.print.assert_called_once()


class TestDisplayBucketCreditLine:
    """Tests for _display_bucket_credit_line function."""

    @patch("cli_output.status.console")
    def test_display_active_purchased_credits(self, mock_console):
        """Test displaying active purchased credits."""
        bucket = {
            "total": 100,
            "remaining": 20,
            "expiry_date": "2028-12-12T00:00:00+00:00",
        }
        _display_bucket_credit_line(bucket, "Purchased")

        call_args = mock_console.print.call_args[0][0]
        assert "Purchased" in call_args
        assert "20 of 100 remaining" in call_args
        assert "expires Dec 12, 2028" in call_args

    @patch("cli_output.status.console")
    def test_display_active_promo_credits(self, mock_console):
        """Test displaying active promo credits."""
        bucket = {
            "total": 100,
            "remaining": 20,
            "expiry_date": "2028-12-12T00:00:00+00:00",
        }
        _display_bucket_credit_line(bucket, "Promo")

        call_args = mock_console.print.call_args[0][0]
        assert "Promo" in call_args
        assert "20 of 100 remaining" in call_args
        assert "expires Dec 12, 2028" in call_args

    @patch("cli_output.status.console")
    def test_display_expired_purchased_credits(self, mock_console):
        """Test displaying expired purchased credits."""
        bucket = {
            "total": 100,
            "remaining": 20,
            "expiry_date": "2024-01-01T00:00:00+00:00",
        }
        _display_bucket_credit_line(bucket, "Purchased")

        call_args = mock_console.print.call_args[0][0]
        assert "expired Jan 1, 2024" in call_args
        assert "remaining" not in call_args

    @patch("cli_output.status.console")
    def test_timezone_naive_datetime(self, mock_console):
        """Test handling of timezone-naive datetime."""
        bucket = {
            "total": 100,
            "remaining": 20,
            "expiry_date": "2026-06-12T00:00:00",  # No timezone
        }
        # Should not raise exception
        _display_bucket_credit_line(bucket, "Purchased")
        mock_console.print.assert_called_once()


class TestDisplayStatusMessage:
    """Tests for _display_status_message function."""

    @patch("cli_output.status.console")
    def test_has_active_plan_credits(self, mock_console):
        """Test when user has active plan credits."""
        plan_credits = {
            "remaining": 10,
            "period_end": "2028-12-01T00:00:00+00:00",
        }
        _display_status_message(plan_credits, [], [])

        # Should not print warning message
        mock_console.print.assert_not_called()

    @patch("cli_output.status.console")
    def test_has_active_purchased_credits(self, mock_console):
        """Test when user has active purchased credits."""
        purchased_credits = [
            {
                "remaining": 20,
                "expiry_date": "2030-06-12T00:00:00+00:00",
            }
        ]
        _display_status_message(None, purchased_credits, [])

        # Should not print warning message
        mock_console.print.assert_not_called()

    @patch("cli_output.status.console")
    def test_has_active_promo_credits(self, mock_console):
        """Test when user only has active promo credits."""
        promo_credits = [
            {
                "remaining": 20,
                "expiry_date": "2030-06-12T00:00:00+00:00",
            }
        ]
        _display_status_message(None, [], promo_credits)

        # Should not print warning message
        mock_console.print.assert_not_called()

    @patch("cli_output.status.console")
    def test_no_credits_remaining(self, mock_console):
        """Test when user has no credits remaining."""
        plan_credits = {
            "remaining": 0,
            "period_end": "2028-12-01T00:00:00+00:00",
        }
        _display_status_message(plan_credits, [], [])

        mock_console.print.assert_called_once()
        call_args = mock_console.print.call_args[0][0]
        assert "No rendering credits remaining" in call_args

    @patch("cli_output.status.console")
    def test_expired_plan_credits(self, mock_console):
        """Test when plan credits are expired."""
        plan_credits = {
            "remaining": 10,
            "period_end": "2024-01-01T00:00:00+00:00",
        }
        _display_status_message(plan_credits, [], [])

        mock_console.print.assert_called_once()
        call_args = mock_console.print.call_args[0][0]
        assert "No rendering credits remaining" in call_args

    @patch("cli_output.status.console")
    def test_null_plan_credits_and_empty_purchased(self, mock_console):
        """Test when plan_credits is None and purchased/promo credits are empty."""
        _display_status_message(None, [], [])

        mock_console.print.assert_called_once()
        call_args = mock_console.print.call_args[0][0]
        assert "No rendering credits remaining" in call_args


class TestPrintStatus:
    """Tests for print_status function."""

    @patch("cli_output.status.codeplain_api.CodeplainAPI")
    @patch("cli_output.status.console")
    def test_valid_client_version(self, mock_console, mock_api_class):
        """Test status display with valid client version."""
        # Setup mock API
        mock_api = Mock()
        mock_api_class.return_value = mock_api
        mock_api.connection_check.return_value = {
            "client_version_valid": True,
            "min_client_version": "0.3.0",
        }
        mock_api.status.return_value = {
            "user": {
                "first_name": "John",
                "last_name": "Doe",
                "email": "john@example.com",
            },
            "api_key_label": "test-key",
            "organization_owner_email": "owner@example.com",
            "plan_credits": {
                "type": "free",
                "total": 50,
                "remaining": 10,
                "period_end": "2028-12-01T00:00:00+00:00",
            },
            "purchased_credits": [],
        }

        print_status("fake-key", "http://localhost:5000", "0.3.0")

        # Verify console.print was called with version info
        calls = [str(call) for call in mock_console.print.call_args_list]
        version_call = next((c for c in calls if "Version: 0.3.0" in c), None)
        assert version_call is not None
        assert "outdated" not in str(version_call)

    @patch("cli_output.status.codeplain_api.CodeplainAPI")
    @patch("cli_output.status.console")
    def test_outdated_client_version(self, mock_console, mock_api_class):
        """Test status display with outdated client version."""
        # Setup mock API
        mock_api = Mock()
        mock_api_class.return_value = mock_api
        mock_api.connection_check.return_value = {
            "client_version_valid": False,
            "min_client_version": "0.5.0",
        }
        mock_api.status.return_value = {
            "user": {
                "first_name": "John",
                "last_name": "Doe",
                "email": "john@example.com",
            },
            "api_key_label": "test-key",
            "organization_owner_email": "owner@example.com",
            "plan_credits": None,
            "purchased_credits": [],
        }

        print_status("fake-key", "http://localhost:5000", "0.2.0")

        # Verify error-styled version message
        calls = [str(call) for call in mock_console.print.call_args_list]
        version_calls = [c for c in calls if "Version: 0.2.0" in c]
        assert len(version_calls) > 0
        outdated_call = next((c for c in version_calls if "outdated" in c), None)
        assert outdated_call is not None
        assert "minimum required: 0.5.0" in outdated_call

    @patch("cli_output.status.codeplain_api.CodeplainAPI")
    @patch("cli_output.status.console")
    def test_no_organization_owner(self, mock_console, mock_api_class):
        """Test status display when no organization owner exists."""
        mock_api = Mock()
        mock_api_class.return_value = mock_api
        mock_api.connection_check.return_value = {
            "client_version_valid": True,
            "min_client_version": "0.3.0",
        }
        mock_api.status.return_value = {
            "user": {
                "first_name": "John",
                "last_name": "Doe",
                "email": "john@example.com",
            },
            "api_key_label": "test-key",
            "organization_owner_email": None,
            "plan_credits": None,
            "purchased_credits": [],
        }

        print_status("fake-key", "http://localhost:5000", "0.3.0")

        # Verify N/A is displayed for organization owner
        calls = [str(call) for call in mock_console.print.call_args_list]
        org_call = next((c for c in calls if "Organization owner" in c), None)
        assert org_call is not None
        assert "N/A" in org_call

    @patch("cli_output.status.codeplain_api.CodeplainAPI")
    @patch("cli_output.status.console")
    def test_multiple_purchased_credit_buckets(self, mock_console, mock_api_class):
        """Test status display with multiple purchased credit buckets."""
        mock_api = Mock()
        mock_api_class.return_value = mock_api
        mock_api.connection_check.return_value = {
            "client_version_valid": True,
            "min_client_version": "0.3.0",
        }
        mock_api.status.return_value = {
            "user": {
                "first_name": "John",
                "last_name": "Doe",
                "email": "john@example.com",
            },
            "api_key_label": "test-key",
            "organization_owner_email": "owner@example.com",
            "plan_credits": None,
            "purchased_credits": [
                {
                    "total": 100,
                    "remaining": 50,
                    "expiry_date": "2026-06-12T00:00:00+00:00",
                },
                {
                    "total": 200,
                    "remaining": 100,
                    "expiry_date": "2026-12-31T00:00:00+00:00",
                },
            ],
        }

        print_status("fake-key", "http://localhost:5000", "0.3.0")

        # Verify both buckets are displayed
        calls = [str(call) for call in mock_console.print.call_args_list]
        purchased_calls = [c for c in calls if "Purchased" in c]
        assert len(purchased_calls) == 2

    @patch("cli_output.status.codeplain_api.CodeplainAPI")
    @patch("cli_output.status.console")
    def test_promo_credit_buckets(self, mock_console, mock_api_class):
        """Test status display includes promo credit buckets."""
        mock_api = Mock()
        mock_api_class.return_value = mock_api
        mock_api.connection_check.return_value = {
            "client_version_valid": True,
            "min_client_version": "0.3.0",
        }
        mock_api.status.return_value = {
            "user": {
                "first_name": "John",
                "last_name": "Doe",
                "email": "john@example.com",
            },
            "api_key_label": "test-key",
            "organization_owner_email": "owner@example.com",
            "plan_credits": None,
            "purchased_credits": [
                {
                    "total": 100,
                    "remaining": 50,
                    "expiry_date": "2030-06-12T00:00:00+00:00",
                },
            ],
            "promo_credits": [
                {
                    "total": 30,
                    "remaining": 15,
                    "expiry_date": "2030-12-31T00:00:00+00:00",
                },
            ],
        }

        print_status("fake-key", "http://localhost:5000", "0.3.0")

        calls = [str(call) for call in mock_console.print.call_args_list]
        promo_calls = [c for c in calls if "Promo" in c]
        assert len(promo_calls) == 1
        # Active credits remain, so no "no credits remaining" warning
        assert not any("No rendering credits remaining" in c for c in calls)

    @patch("cli_output.status.codeplain_api.CodeplainAPI")
    @patch("cli_output.status.console")
    def test_missing_promo_credits_key_is_backward_compatible(self, mock_console, mock_api_class):
        """Test status display when API response omits promo_credits (older API)."""
        mock_api = Mock()
        mock_api_class.return_value = mock_api
        mock_api.connection_check.return_value = {
            "client_version_valid": True,
            "min_client_version": "0.3.0",
        }
        mock_api.status.return_value = {
            "user": {
                "first_name": "John",
                "last_name": "Doe",
                "email": "john@example.com",
            },
            "api_key_label": "test-key",
            "organization_owner_email": "owner@example.com",
            "plan_credits": {
                "type": "free",
                "total": 50,
                "remaining": 10,
                "period_end": "2030-12-01T00:00:00+00:00",
            },
            "purchased_credits": [],
            # promo_credits intentionally omitted
        }

        # Should not raise
        print_status("fake-key", "http://localhost:5000", "0.3.0")

        calls = [str(call) for call in mock_console.print.call_args_list]
        assert not any("Promo" in c for c in calls)


class TestVersionFlag:
    """Tests for --version flag."""

    def test_version_flag_without_filename(self, capsys):
        """Test that --version works without providing a filename."""
        import sys

        import plain2code

        # Save original argv
        original_argv = sys.argv
        try:
            sys.argv = ["plain2code.py", "--version"]
            # This should not raise an error about missing filename
            plain2code.main()

            captured = capsys.readouterr()
            assert "codeplain version" in captured.out
        finally:
            sys.argv = original_argv
