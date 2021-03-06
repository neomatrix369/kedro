# Copyright 2020 QuantumBlack Visual Analytics Limited
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
# EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES
# OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE, AND
# NONINFRINGEMENT. IN NO EVENT WILL THE LICENSOR OR OTHER CONTRIBUTORS
# BE LIABLE FOR ANY CLAIM, DAMAGES, OR OTHER LIABILITY, WHETHER IN AN
# ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF, OR IN
# CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
#
# The QuantumBlack Visual Analytics Limited ("QuantumBlack") name and logo
# (either separately or in combination, "QuantumBlack Trademarks") are
# trademarks of QuantumBlack. The License does not grant you any right or
# license to the QuantumBlack Trademarks. You may not use the QuantumBlack
# Trademarks or any confusingly similar mark as a trademark for your product,
#     or use the QuantumBlack Trademarks in any other manner that might cause
# confusion in the marketplace, including but not limited to in advertising,
# on websites, or on software.
#
# See the License for the specific language governing permissions and
# limitations under the License.

import shutil
import sys
import tempfile
from importlib import import_module
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner
from pandas import DataFrame

from kedro.cli.cli import cli
from kedro.cli.pipeline import _sync_dirs
from kedro.context import load_context
from kedro.extras.datasets.pandas import CSVDataSet

REPO_NAME = "dummy_project"
PACKAGE_NAME = "dummy_package"
SINGLE_PIPELINE = ["single_pipeline"]
MULTIPLE_PIPELINES = ["pipeline1", "pipeline2"]


@pytest.fixture(params=[None])
def pipelines_to_create(request):
    return request.param or []


@pytest.fixture(scope="module")
def fake_root_dir():
    # using tempfile as tmp_path fixture doesn't support session scope
    with tempfile.TemporaryDirectory() as tmp_root:
        yield Path(tmp_root).resolve()


@pytest.fixture(scope="module")
def dummy_config(fake_root_dir):
    config = {
        "project_name": "Dummy Project",
        "repo_name": REPO_NAME,
        "python_package": PACKAGE_NAME,
        "include_example": True,
        "output_dir": str(fake_root_dir),
    }

    config_path = fake_root_dir / "dummy_config.yml"
    with config_path.open("w") as f:
        yaml.dump(config, f)

    return config_path


@pytest.fixture(scope="module")
def dummy_project(fake_root_dir, dummy_config):
    CliRunner().invoke(cli, ["new", "-c", str(dummy_config)])
    project_path = fake_root_dir / REPO_NAME
    src_path = project_path / "src"

    # NOTE: Here we load a couple of modules, as they would be imported in
    # the code and tests.
    # It's safe to remove the new entries from path due to the python
    # module caching mechanism. Any `reload` on it will not work though.
    old_path = sys.path.copy()
    sys.path = [str(project_path), str(src_path)] + sys.path

    import_module("kedro_cli")
    import_module(PACKAGE_NAME)

    sys.path = old_path

    yield project_path

    del sys.modules["kedro_cli"]
    del sys.modules[PACKAGE_NAME]


@pytest.fixture(scope="module")
def fake_kedro_cli(dummy_project):  # pylint: disable=unused-argument
    """
    A small helper to pass kedro_cli into tests without importing.
    It only becomes available after `dummy_project` fixture is applied,
    that's why it can't be done on module level.
    """
    yield import_module("kedro_cli")


@pytest.fixture(autouse=True)
def cleanup_pipeline(pipelines_to_create, dummy_project):
    yield

    # remove created pipeline files after the test
    for pipeline in pipelines_to_create:
        pipe_path = dummy_project / "src" / PACKAGE_NAME / "pipelines" / pipeline
        shutil.rmtree(str(pipe_path))

        confs = dummy_project / "conf"
        for each in confs.glob(f"*/{pipeline}"):  # clean all config envs
            shutil.rmtree(str(each))

        tests = dummy_project / "src" / "tests" / "pipelines" / pipeline
        shutil.rmtree(str(tests))


@pytest.fixture
def chdir_to_dummy_project(dummy_project, monkeypatch):
    monkeypatch.chdir(str(dummy_project))


@pytest.fixture
def patch_log(mocker):
    mocker.patch("logging.config.dictConfig")


LETTER_ERROR = "It must contain only letters, digits, and/or underscores."
FIRST_CHAR_ERROR = "It must start with a letter or underscore."
TOO_SHORT_ERROR = "It must be at least 2 characters long."


@pytest.mark.usefixtures("chdir_to_dummy_project", "patch_log")
class TestPipelineCreateCommand:
    @pytest.mark.parametrize(
        "pipelines_to_create", [SINGLE_PIPELINE, MULTIPLE_PIPELINES], indirect=True
    )
    @pytest.mark.parametrize("env", [None, "local"])
    def test_create_pipeline(  # pylint: disable=too-many-locals
        self, dummy_project, fake_kedro_cli, pipelines_to_create, env
    ):
        """Test creation of one or multiple pipelines at once"""
        pipelines_dir = dummy_project / "src" / PACKAGE_NAME / "pipelines"
        assert pipelines_dir.is_dir()

        for pipeline_name in pipelines_to_create:
            assert not (pipelines_dir / pipeline_name).exists()

        cmd = ["pipeline", "create"] + pipelines_to_create
        cmd += ["-e", env] if env else []
        result = CliRunner().invoke(fake_kedro_cli.cli, cmd)
        assert result.exit_code == 0
        assert (
            "To be able to run new pipelines, you will need "
            "to add them to `create_pipelines()`" in result.output
        )

        for pipeline_name in pipelines_to_create:
            # pipeline
            assert f"Creating a pipeline `{pipeline_name}`: OK" in result.output
            assert f"Location: `{pipelines_dir / pipeline_name}`" in result.output

            # config
            conf_env = env or "base"
            conf_dir = (dummy_project / "conf" / conf_env / pipeline_name).resolve()
            expected_configs = {"catalog.yml", "parameters.yml"}
            actual_configs = {f.name for f in conf_dir.iterdir()}
            assert actual_configs == expected_configs

            # tests
            test_dir = dummy_project / "src" / "tests" / "pipelines" / pipeline_name
            expected_files = {"__init__.py", "test_pipeline.py"}
            actual_files = {f.name for f in test_dir.iterdir()}
            assert actual_files == expected_files

    @pytest.mark.parametrize(
        "pipelines_to_create", [SINGLE_PIPELINE, MULTIPLE_PIPELINES], indirect=True
    )
    @pytest.mark.parametrize("env", [None, "local"])
    def test_create_pipeline_skip_config(
        self, dummy_project, fake_kedro_cli, pipelines_to_create, env
    ):
        """Test creation of one or multiple pipelines at once"""

        cmd = ["pipeline", "create", "--skip-config"] + pipelines_to_create
        cmd += ["-e", env] if env else []
        result = CliRunner().invoke(fake_kedro_cli.cli, cmd)
        assert result.exit_code == 0
        assert (
            "To be able to run new pipelines, you will need "
            "to add them to `create_pipelines()`" in result.output
        )

        for pipeline_name in pipelines_to_create:
            assert f"Creating a pipeline `{pipeline_name}`: OK" in result.output

            conf_dirs = list((dummy_project / "conf").glob(f"*/{pipeline_name}"))
            assert conf_dirs == []  # no configs created for the pipeline

            test_dir = dummy_project / "src" / "tests" / "pipelines" / pipeline_name
            assert test_dir.is_dir()

    @pytest.mark.parametrize("pipelines_to_create", [SINGLE_PIPELINE], indirect=True)
    def test_catalog_and_params(
        self, dummy_project, fake_kedro_cli, pipelines_to_create
    ):
        """Test that catalog and configs generated in pipeline sections
        propagate into the context"""
        pipelines_dir = dummy_project / "src" / PACKAGE_NAME / "pipelines"
        assert pipelines_dir.is_dir()

        cmd = ["pipeline", "create"] + pipelines_to_create
        result = CliRunner().invoke(fake_kedro_cli.cli, cmd)
        assert result.exit_code == 0

        # write pipeline catalog
        catalog_dict = {
            "ds_from_pipeline": {
                "type": "pandas.CSVDataSet",
                "filepath": "data/01_raw/iris.csv",
            }
        }
        catalog = (
            dummy_project / "conf" / "base" / pipelines_to_create[0] / "catalog.yml"
        )
        with catalog.open("w") as f:
            yaml.dump(catalog_dict, f)

        # write pipeline parameters
        params_dict = {"params_from_pipeline": {"p1": [1, 2, 3], "p2": None}}
        params = (
            dummy_project / "conf" / "base" / pipelines_to_create[0] / "parameters.yml"
        )
        with params.open("w") as f:
            yaml.dump(params_dict, f)

        ctx = load_context(Path.cwd())
        assert isinstance(ctx.catalog._data_sets["ds_from_pipeline"], CSVDataSet)
        assert isinstance(ctx.catalog.load("ds_from_pipeline"), DataFrame)
        assert ctx.params["params_from_pipeline"] == params_dict["params_from_pipeline"]

    @pytest.mark.parametrize("pipelines_to_create", [SINGLE_PIPELINE], indirect=True)
    def test_skip_copy(self, dummy_project, fake_kedro_cli, pipelines_to_create):
        """Test skipping the copy of conf and test files if those already exist"""
        # create conf and tests
        pipeline_name = pipelines_to_create[0]

        # touch pipeline
        catalog = dummy_project / "conf" / "base" / pipeline_name / "catalog.yml"
        catalog.parent.mkdir(parents=True)
        catalog.touch()

        # touch test __init__.py
        tests_init = (
            dummy_project
            / "src"
            / "tests"
            / "pipelines"
            / pipeline_name
            / "__init__.py"
        )
        tests_init.parent.mkdir(parents=True)
        tests_init.touch()

        cmd = ["pipeline", "create"] + pipelines_to_create
        result = CliRunner().invoke(fake_kedro_cli.cli, cmd)

        assert result.exit_code == 0
        assert "catalog.yml`: SKIPPED" in result.output
        assert "__init__.py`: SKIPPED" in result.output
        assert result.output.count("SKIPPED") == 2  # only 2 files skipped

    @pytest.mark.parametrize("pipelines_to_create", [SINGLE_PIPELINE], indirect=True)
    def test_failed_copy(
        self, dummy_project, fake_kedro_cli, pipelines_to_create, mocker
    ):
        """Test skipping the copy of file that already exist"""
        error = Exception("Mock exception")
        mocked_copy = mocker.patch("shutil.copyfile", side_effect=error)

        cmd = ["pipeline", "create"] + pipelines_to_create
        result = CliRunner().invoke(fake_kedro_cli.cli, cmd)
        mocked_copy.assert_called_once()
        assert result.exit_code
        assert result.output.count("FAILED") == 1
        assert result.exception is error

        # but the pipeline is created anyways
        pipelines_dir = dummy_project / "src" / PACKAGE_NAME / "pipelines"
        for pipe_name in pipelines_to_create:
            assert (pipelines_dir / pipe_name / "pipeline.py").is_file()

    def test_no_pipelines_to_create(
        self, dummy_project, fake_kedro_cli, pipelines_to_create
    ):
        """Test output message when no pipeline name were provided"""
        pipelines_dir = dummy_project / "src" / PACKAGE_NAME / "pipelines"
        assert pipelines_dir.is_dir()

        result = CliRunner().invoke(
            fake_kedro_cli.cli, ["pipeline", "create"] + pipelines_to_create
        )
        assert result.exit_code == 0
        assert "No pipeline names provided, exiting." in result.output

    @pytest.mark.parametrize(
        "bad_names,error_message",
        [
            (["bad name"], LETTER_ERROR),
            (["bad%name"], LETTER_ERROR),
            (["1bad"], FIRST_CHAR_ERROR),
            (["a"], TOO_SHORT_ERROR),
            (["good_name", "b"], TOO_SHORT_ERROR),
        ],
    )
    def test_bad_pipeline_name(self, fake_kedro_cli, bad_names, error_message):
        """Test error message bad pipeline name provided"""
        result = CliRunner().invoke(
            fake_kedro_cli.cli, ["pipeline", "create"] + bad_names
        )
        assert result.exit_code
        assert error_message in result.output

    @pytest.mark.parametrize(
        "pipelines_to_create", [SINGLE_PIPELINE, MULTIPLE_PIPELINES], indirect=True
    )
    def test_duplicate_pipeline_name(
        self, dummy_project, fake_kedro_cli, pipelines_to_create
    ):
        """Test error when attempting to create pipelines with duplicate names"""
        pipelines_dir = dummy_project / "src" / PACKAGE_NAME / "pipelines"
        assert pipelines_dir.is_dir()

        pipelines_to_create = pipelines_to_create.copy()
        duplicate = pipelines_to_create[0]
        pipelines_to_create.append(duplicate)

        cmd = ["pipeline", "create"] + pipelines_to_create
        result = CliRunner().invoke(fake_kedro_cli.cli, cmd)
        assert result.exit_code
        assert f"Creating a pipeline `{duplicate}`: FAILED" in result.output
        assert "directory already exists" in result.output

    def test_bad_env(self, fake_kedro_cli):
        """Test error when provided conf environment does not exist"""
        env = "no_such_env"
        cmd = ["pipeline", "create", "-e", env] + SINGLE_PIPELINE
        result = CliRunner().invoke(fake_kedro_cli.cli, cmd)
        assert result.exit_code
        assert f"Unable to load Kedro context with environment `{env}`" in result.output


@pytest.mark.usefixtures("chdir_to_dummy_project", "patch_log")
class TestPipelineListCommand:
    @pytest.fixture
    def yaml_dump_mock(self, mocker):
        return mocker.patch("yaml.dump", return_value="Result YAML")

    @pytest.fixture
    def pipelines_dict(self):
        pipelines = {
            "de": ["Split Data (split_data)"],
            "ds": [
                "Train Model (train_model)",
                "Predict (predict)",
                "Report Accuracy (report_accuracy)",
            ],
        }
        pipelines["__default__"] = pipelines["de"] + pipelines["ds"]
        return pipelines

    def test_show_list_of_pipelines(
        self, fake_kedro_cli, yaml_dump_mock, pipelines_dict
    ):
        result = CliRunner().invoke(
            fake_kedro_cli.cli, ["pipeline", "list", "--simple"]
        )

        assert not result.exit_code
        yaml_dump_mock.assert_called_once_with(sorted(pipelines_dict.keys()))

    def test_show_specific_pipelines(
        self, fake_kedro_cli, yaml_dump_mock, pipelines_dict
    ):
        pipe_name = "de"
        result = CliRunner().invoke(fake_kedro_cli.cli, ["pipeline", "list", pipe_name])

        assert not result.exit_code
        expected_dict = {pipe_name: pipelines_dict[pipe_name]}
        yaml_dump_mock.assert_called_once_with(expected_dict)

    def test_describe_all_pipelines(
        self, fake_kedro_cli, yaml_dump_mock, pipelines_dict
    ):
        result = CliRunner().invoke(fake_kedro_cli.cli, ["pipeline", "list"])

        assert not result.exit_code
        yaml_dump_mock.assert_called_once_with(pipelines_dict)

    def test_not_found_pipeline(self, fake_kedro_cli):
        result = CliRunner().invoke(fake_kedro_cli.cli, ["pipeline", "list", "missing"])

        assert result.exit_code
        expected_output = (
            "Error: missing pipeline not found. Existing pipelines: "
            "__default__, de, ds\n"
        )
        assert result.output == expected_output


class TestSyncDirs:
    @pytest.fixture(autouse=True)
    def mock_click(self, mocker):
        mocker.patch("click.secho")

    @pytest.fixture
    def source(self, tmp_path) -> Path:
        source_dir = Path(tmp_path) / "source"
        source_dir.mkdir()
        (source_dir / "existing").mkdir()
        (source_dir / "existing" / "source_file").touch()
        (source_dir / "existing" / "common").write_text("source")
        (source_dir / "new").mkdir()
        (source_dir / "new" / "source_file").touch()
        return source_dir

    def test_sync_target_exists(self, source, tmp_path):
        """Test _sync_dirs utility function if target exists."""
        target = Path(tmp_path) / "target"
        target.mkdir()
        (target / "existing").mkdir()
        (target / "existing" / "target_file").touch()
        (target / "existing" / "common").write_text("target")

        _sync_dirs(source, target)

        assert (source / "existing" / "source_file").is_file()
        assert (source / "existing" / "common").read_text() == "source"
        assert not (source / "existing" / "target_file").exists()
        assert (source / "new" / "source_file").is_file()

        assert (target / "existing" / "source_file").is_file()
        assert (target / "existing" / "common").read_text() == "target"
        assert (target / "existing" / "target_file").exists()
        assert (target / "new" / "source_file").is_file()

    def test_sync_no_target(self, source, tmp_path):
        """Test _sync_dirs utility function if target doesn't exist."""
        target = Path(tmp_path) / "target"

        _sync_dirs(source, target)

        assert (source / "existing" / "source_file").is_file()
        assert (source / "existing" / "common").read_text() == "source"
        assert not (source / "existing" / "target_file").exists()
        assert (source / "new" / "source_file").is_file()

        assert (target / "existing" / "source_file").is_file()
        assert (target / "existing" / "common").read_text() == "source"
        assert not (target / "existing" / "target_file").exists()
        assert (target / "new" / "source_file").is_file()
