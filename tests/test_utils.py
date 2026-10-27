def test_normalize_model_name():
    from src.utils import normalize_model_name

    cases = {
        " Space-Bunny-Free ": "stealth/space-bunny-alpha",
        "STEALTH/SPACE-BUNNY-ALPHA": "stealth/space-bunny-alpha",
        "ling-3.1-flash-free": "inclusionai/ling-3.1-flash",
        " Ling-3.1-Flash-Free ": "inclusionai/ling-3.1-flash",
        " MiniMax-M2.7 ": "minimax-m2.7",
        "deepseek-v4.1-flash-free": "deepseek-v4.1-flash-free",
        "vendor/model:free": "vendor/model:free",
        "vendor/model:batch": "vendor/model:batch",
        "claude-sonnet-4-5-20250929": "claude-sonnet-4-5-20250929",
        "openrouter/vendor/custom-model": "openrouter/vendor/custom-model",
        "": "",
        " \t\n": "",
    }
    for name, expected in cases.items():
        assert normalize_model_name(name) == expected
        assert normalize_model_name(expected) == expected


def test_normalize_provider_name():
    from src.utils import normalize_provider_name

    assert normalize_provider_name("OpenAI") == "openai"
    assert normalize_provider_name("MiniMax") == "minimax"
    assert normalize_provider_name("minimaxi") == "minimax"
    assert normalize_provider_name("MiniMax-CN") == "minimax"
    assert normalize_provider_name("api.minimaxi.com") == "minimax"
    assert normalize_provider_name("My-Custom-Provider") == "my-custom-provider"
    assert normalize_provider_name(" OpenAI ") == "openai"
    assert normalize_provider_name("") == ""


def test_secs_to_micros_integer(utils_module):
    assert utils_module.secs_to_micros(1776384000) == 1776384000000000


def test_secs_to_micros_float(utils_module):
    assert utils_module.secs_to_micros(1776384000.5) == 1776384000500000


def test_secs_to_micros_zero(utils_module):
    assert utils_module.secs_to_micros(0) == 0


def test_micros_to_secs(utils_module):
    assert utils_module.micros_to_secs(1776384000000000) == 1776384000.0


def test_micros_to_secs_zero(utils_module):
    assert utils_module.micros_to_secs(0) == 0.0


def test_secs_to_micros_roundtrip(utils_module):
    original = 1776384000
    assert utils_module.micros_to_secs(utils_module.secs_to_micros(original)) == float(
        original
    )
