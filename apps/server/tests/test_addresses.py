"""Address normalisation.

Amazon builds Send to Kindle addresses with a random mixed-case suffix, and
RFC 5321 makes the local part case-sensitive — only the receiving server may
interpret it. Lowercasing one produces an address that does not exist, and
Amazon discards mail to it without a bounce.
"""
import pytest

from wikindle.addresses import normalise_email, normalise_kindle_address


def test_preserves_the_case_of_the_local_part():
    address = "selina.liball16_nPgTrV@kindle.com"
    assert normalise_kindle_address(address) == address


def test_lowercases_only_the_domain():
    assert normalise_kindle_address("Bob_aBcDeF@KINDLE.COM") == "Bob_aBcDeF@kindle.com"


def test_strips_surrounding_whitespace():
    assert normalise_kindle_address("  a_Bc@kindle.com \n") == "a_Bc@kindle.com"


def test_an_all_lowercase_address_is_untouched():
    address = "florian.rammerstorfer1_pi4jay@kindle.com"
    assert normalise_kindle_address(address) == address


@pytest.mark.parametrize(
    "address",
    [
        "me@example.com",          # an ordinary mailbox, not a Kindle
        "me@notkindle.com",        # merely ends with the right letters
        "me@kindle.com.evil.test",
        "not-an-address",
        "",
    ],
)
def test_rejects_things_that_are_not_kindle_addresses(address):
    with pytest.raises(ValueError):
        normalise_kindle_address(address)


def test_accepts_kindle_subdomains():
    assert normalise_kindle_address("a_B@free.kindle.com") == "a_B@free.kindle.com"


def test_contact_email_keeps_its_local_part_too():
    """Most providers treat the local part case-insensitively, but it is not
    ours to assume — and there is nothing to gain by mangling it."""
    assert normalise_email("Florian.R@Example.COM", "contact email") == (
        "Florian.R@example.com"
    )
