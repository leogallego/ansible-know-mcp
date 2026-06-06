"""Shared test fixtures."""

import json

import pytest


def pytest_addoption(parser):
    parser.addoption(
        "--run-integration",
        action="store_true",
        default=False,
        help="Run integration tests (requires ansible-core and network access)",
    )


def pytest_collection_modifyitems(config, items):
    if config.getoption("--run-integration"):
        return
    skip_integration = pytest.mark.skip(reason="needs --run-integration option to run")
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip_integration)

SAMPLE_MODULE_DOC = {
    "ansible.builtin.package": {
        "doc": {
            "module": "ansible.builtin.package",
            "short_description": "Generic OS package manager",
            "description": ["Installs, upgrades, removes packages using the OS package manager."],
            "options": {
                "name": {
                    "description": [
                        "Package name, or package specifier with version."
                    ],
                    "type": "str",
                    "required": True,
                },
                "state": {
                    "description": [
                        "Whether to install (present), or remove (absent) a package."
                    ],
                    "type": "str",
                    "required": True,
                    "choices": ["present", "absent", "latest"],
                },
                "use": {
                    "description": [
                        "The required package manager module to use."
                    ],
                    "type": "str",
                    "required": False,
                    "default": "auto",
                    "choices": ["auto", "apt", "dnf", "yum"],
                },
            },
        },
        "examples": (
            "- name: Install ntpdate\n"
            "  ansible.builtin.package:\n"
            "    name: ntpdate\n"
            "    state: present\n"
        ),
    }
}

SAMPLE_API_MODULE_DOC = {
    "netbox.netbox.netbox_device": {
        "doc": {
            "module": "netbox.netbox.netbox_device",
            "short_description": "Create, update or delete devices within NetBox",
            "description": ["Creates, updates or removes devices from NetBox."],
            "options": {
                "data": {
                    "description": ["Defines the device configuration"],
                    "type": "dict",
                    "required": True,
                },
                "netbox_url": {
                    "description": ["The URL of the NetBox instance."],
                    "type": "str",
                    "required": True,
                },
                "netbox_token": {
                    "description": ["The NetBox API token."],
                    "type": "str",
                    "required": True,
                },
                "state": {
                    "description": ["The state of the object."],
                    "type": "str",
                    "required": False,
                    "default": "present",
                    "choices": ["present", "absent"],
                },
                "validate_certs": {
                    "description": ["If no, SSL certificates will not be validated."],
                    "type": "raw",
                    "required": False,
                    "default": True,
                },
            },
        },
        "examples": (
            "- name: Test NetBox modules\n"
            "  connection: local\n"
            "  hosts: localhost\n"
            "  gather_facts: false\n"
            "  tasks:\n"
            "    - name: Create device\n"
            "      netbox.netbox.netbox_device:\n"
            "        netbox_url: http://netbox.local\n"
            "        netbox_token: thisIsMyToken\n"
            "        data:\n"
            "          name: Test Device\n"
            "          device_type: C9410R\n"
            "          site: Main\n"
            "        state: present\n"
        ),
    }
}


SAMPLE_MODULE_LIST = {
    "ansible.builtin.package": "Generic OS package manager",
    "ansible.builtin.apt": "Manages apt-packages",
    "ansible.builtin.yum": "Manages packages with the yum package manager",
    "community.general.redis": "Various redis commands, replica and flush",
}


@pytest.fixture
def sample_module_doc():
    return SAMPLE_MODULE_DOC


@pytest.fixture
def sample_module_doc_json():
    return json.dumps(SAMPLE_MODULE_DOC)


@pytest.fixture
def sample_api_module_doc():
    return SAMPLE_API_MODULE_DOC


@pytest.fixture
def sample_api_module_doc_json():
    return json.dumps(SAMPLE_API_MODULE_DOC)


@pytest.fixture
def sample_module_list():
    return SAMPLE_MODULE_LIST


@pytest.fixture
def sample_module_list_json():
    return json.dumps(SAMPLE_MODULE_LIST)
