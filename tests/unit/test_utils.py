# MIT License
#
# Copyright (c) 2018-2019 Red Hat, Inc.

# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.
from pathlib import Path

import pytest

from sandcastle.utils import Chmodo


@pytest.fixture()
def good_testing_tree(tmpdir) -> Path:
    p = Path(tmpdir)
    test_dir = p.joinpath("tree")
    test_dir.mkdir()
    d = test_dir.joinpath("d")
    d.mkdir()
    d.joinpath("f").write_text("a")
    d.joinpath("f").chmod(0o777)
    e = test_dir.joinpath("e")
    e.mkdir()
    e.joinpath("g").write_text("b")
    e.joinpath("g").chmod(0o640)
    test_dir.joinpath("h").mkdir()
    test_dir.joinpath("h").chmod(0o777)
    return test_dir


def test_chmodo_list_files(good_testing_tree):
    tree = good_testing_tree
    c = Chmodo(path=tree)
    result = c.get_files_and_mode()
    assert (tree.joinpath("d"), "0o40775") in result
    assert (tree.joinpath("d").joinpath("f"), "0o100777") in result
    assert (tree.joinpath("e"), "0o40775") in result
    assert (tree.joinpath("e").joinpath("g"), "0o100640") in result
    assert (tree.joinpath("h"), "0o40777") in result


def test_chmodo_the_script(good_testing_tree):
    tree = good_testing_tree
    c = Chmodo(path=tree)
    result = c.get_shell_script()
    assert "chmod 775 d" in result
    assert "chmod 777 d/f" in result
    assert "chmod 775 e" in result
    assert "chmod 640 e/g" in result
    assert "chmod 777 h" in result
