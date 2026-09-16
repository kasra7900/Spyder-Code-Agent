import sys
import types

from spyder_code_agent import doctor


def test_doctor_explains_when_run_outside_spyder(monkeypatch):
    monkeypatch.delitem(sys.modules, "spyder", raising=False)
    monkeypatch.setattr(doctor.importlib, "import_module", lambda name: (_ for _ in ()).throw(ImportError))

    healthy, messages = doctor.check_host_environment()

    assert not healthy
    assert any("kernel/project environment" in message for message in messages)


def test_doctor_accepts_matching_entry_point(monkeypatch):
    fake_spyder = types.SimpleNamespace(__version__="6.1.0")
    plugin_class = type(
        "Plugin",
        (),
        {"NAME": "code_agent", "on_initialize": lambda self: None},
    )
    entry_point = types.SimpleNamespace(load=lambda: plugin_class)
    monkeypatch.setattr(doctor.importlib, "import_module", lambda name: fake_spyder)
    monkeypatch.setattr(doctor, "_matching_entry_points", lambda: [entry_point])

    healthy, messages = doctor.check_host_environment()

    assert healthy
    assert any("imports successfully" in message for message in messages)
