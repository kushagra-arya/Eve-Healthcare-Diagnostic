import pytest

from app.bookings import validate_transition
from app.exceptions import AppError
from app.models import BookingStatus


@pytest.mark.parametrize(
    "target", [BookingStatus.CONFIRMED, BookingStatus.FAILED, BookingStatus.CANCELLED]
)
def test_pending_can_enter_a_terminal_state(target):
    validate_transition(BookingStatus.PENDING, target)


@pytest.mark.parametrize(
    "current", [BookingStatus.CONFIRMED, BookingStatus.FAILED, BookingStatus.CANCELLED]
)
def test_terminal_states_cannot_be_reopened(current):
    with pytest.raises(AppError) as exc:
        validate_transition(current, BookingStatus.CONFIRMED)
    assert exc.value.status_code == 409
