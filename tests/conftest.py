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

SAMPLE_ROLE_DOC = {
    "fedora.linux_system_roles.gfs2": {
        "collection": "fedora.linux_system_roles",
        "entry_points": {
            "main": {
                "description": "The gfs2 role.",
                "options": {
                    "gfs2_cluster_name": {
                        "description": "The name of the cluster.",
                        "required": True,
                        "type": "str",
                    },
                    "gfs2_enable_repos": {
                        "description": "Whether to enable required repos.",
                        "required": False,
                        "type": "bool",
                    },
                },
            },
        },
    },
}

SAMPLE_ROLE_LIST = {
    "fedora.linux_system_roles.timesync": {
        "collection": "fedora.linux_system_roles",
        "description": "UNDOCUMENTED",
        "entry_points": {},
    },
    "fedora.linux_system_roles.gfs2": {
        "collection": "fedora.linux_system_roles",
        "description": "The gfs2 role.",
        "entry_points": {"main": {}},
    },
}

SAMPLE_ROLE_README_HTML = """
<h1>Timesync Role</h1>
<p>Configure time synchronization using NTP or PTP.</p>
<h2>Role Variables</h2>
<table>
<thead><tr><th>Variable</th><th>Default</th><th>Description</th></tr></thead>
<tbody>
<tr><td>timesync_ntp_servers</td><td>[]</td><td>List of NTP servers</td></tr>
<tr><td>timesync_ptp_domains</td><td>[]</td><td>List of PTP domains</td></tr>
</tbody>
</table>
<h2>Example Playbook</h2>
<pre><code>---
- hosts: all
  roles:
    - fedora.linux_system_roles.timesync
</code></pre>
<h2>Dependencies</h2>
<p>None.</p>
"""

SAMPLE_ROLE_README_HTML_HEADING_VARS = """
<h1>SAP Role</h1>
<p>Manage SAP operations on target hosts.</p>
<h2>Role Variables</h2>
<h4>sap_state</h4>
<p>Type: str</p>
<p>Default: present</p>
<p>Required: false</p>
<p>The desired state of SAP.</p>
<h4>sap_instance_number</h4>
<p>Type: str</p>
<p>Required: true</p>
<p>The SAP instance number.</p>
<h2>Example Playbook</h2>
<pre><code>---
- hosts: all
  roles:
    - sap.sap_operations.sap_role
</code></pre>
"""

SAMPLE_ROLE_README_HTML_CODEBLOCK_VARS = """
<h1>Docker Role</h1>
<p>Install and configure Docker.</p>
<h2>Role Variables</h2>
<pre><code>docker_edition: ce</code></pre>
<p>The edition of Docker to install (ce or ee).</p>
<pre><code>docker_packages_state: present</code></pre>
<p>State for Docker packages.</p>
"""

SAMPLE_PLUGIN_DOC = {
    "netbox.netbox.nb_lookup": {
        "doc": {
            "name": "nb_lookup",
            "short_description": "Queries and returns elements from NetBox",
            "description": [
                "Queries NetBox via its API to return virtually any information",
                "capable of being stored in NetBox.",
            ],
            "options": {
                "api_endpoint": {
                    "description": ["The URL to the NetBox instance"],
                    "type": "str",
                    "required": True,
                },
                "token": {
                    "description": ["The API token for NetBox"],
                    "type": "str",
                    "required": True,
                },
                "api_filter": {
                    "description": ["The api_filter to use"],
                    "type": "str",
                    "required": False,
                },
            },
        },
        "examples": (
            "- name: Obtain list of sites from NetBox\n"
            "  debug:\n"
            "    msg: \"{{ query('netbox.netbox.nb_lookup', 'sites', api_endpoint='http://localhost', token='mytoken') }}\"\n"
        ),
    },
}

SAMPLE_PLUGIN_LIST = {
    "netbox.netbox.nb_lookup": "Queries and returns elements from NetBox",
    "ansible.builtin.env": "Read the value of environment variables",
    "ansible.builtin.file": "Return file contents",
    "community.general.bitwarden": "Retrieve secrets from Bitwarden",
}

SAMPLE_DOCS_BLOB_WITH_ROLES = {
    "docs_blob": {
        "contents": [
            {
                "content_type": "module",
                "content_name": "some_module",
                "doc_strings": {
                    "doc": {
                        "short_description": "A module",
                        "description": [],
                        "options": [],
                    },
                    "examples": "",
                    "return": [],
                    "metadata": {},
                },
            },
            {
                "content_type": "role",
                "content_name": "timesync",
                "doc_strings": {},
                "readme_file": "README.md",
                "readme_html": SAMPLE_ROLE_README_HTML.strip(),
            },
            {
                "content_type": "role",
                "content_name": "network",
                "doc_strings": {},
                "readme_file": "README.md",
                "readme_html": "<h1>Network</h1><p>Configure networking.</p>",
            },
        ],
    },
}

SAMPLE_DOCS_BLOB_WITH_PLUGINS = {
    "docs_blob": {
        "contents": [
            {
                "content_type": "module",
                "content_name": "netbox_device",
                "doc_strings": {
                    "doc": {
                        "short_description": "Create, update or delete devices",
                        "options": [],
                    },
                    "examples": "",
                    "return": [],
                    "metadata": {},
                },
            },
            {
                "content_type": "lookup",
                "content_name": "nb_lookup",
                "doc_strings": {
                    "doc": {
                        "short_description": "Queries and returns elements from NetBox",
                        "description": ["Queries NetBox via its API."],
                        "options": [
                            {"name": "api_endpoint", "type": "str", "required": True,
                             "description": ["The URL to the NetBox instance"]},
                            {"name": "token", "type": "str", "required": True,
                             "description": ["The API token"]},
                        ],
                    },
                    "examples": "- debug: msg=\"{{ query('netbox.netbox.nb_lookup', 'sites') }}\"",
                    "return": [],
                    "metadata": {},
                },
            },
            {
                "content_type": "filter",
                "content_name": "nb_filter",
                "doc_strings": {
                    "doc": {
                        "short_description": "Filter NetBox data",
                        "description": ["Filters NetBox query results."],
                        "options": [],
                    },
                    "examples": "",
                    "return": [],
                    "metadata": {},
                },
            },
            {
                "content_type": "inventory",
                "content_name": "nb_inventory",
                "doc_strings": {
                    "doc": {
                        "short_description": "NetBox inventory source",
                        "description": ["Dynamic inventory from NetBox."],
                        "options": [
                            {"name": "api_endpoint", "type": "str", "required": True,
                             "description": ["The URL to the NetBox instance"]},
                        ],
                    },
                    "examples": "",
                    "return": [],
                    "metadata": {},
                },
            },
        ],
    },
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


@pytest.fixture
def sample_role_doc():
    return SAMPLE_ROLE_DOC


@pytest.fixture
def sample_role_doc_json():
    return json.dumps(SAMPLE_ROLE_DOC)


@pytest.fixture
def sample_role_list():
    return SAMPLE_ROLE_LIST


@pytest.fixture
def sample_role_list_json():
    return json.dumps(SAMPLE_ROLE_LIST)


@pytest.fixture
def sample_role_readme_html():
    return SAMPLE_ROLE_README_HTML


@pytest.fixture
def sample_docs_blob_with_roles():
    return SAMPLE_DOCS_BLOB_WITH_ROLES


@pytest.fixture
def sample_plugin_doc():
    return SAMPLE_PLUGIN_DOC


@pytest.fixture
def sample_plugin_doc_json():
    return json.dumps(SAMPLE_PLUGIN_DOC)


@pytest.fixture
def sample_plugin_list():
    return SAMPLE_PLUGIN_LIST


@pytest.fixture
def sample_plugin_list_json():
    return json.dumps(SAMPLE_PLUGIN_LIST)


@pytest.fixture
def sample_docs_blob_with_plugins():
    return SAMPLE_DOCS_BLOB_WITH_PLUGINS
