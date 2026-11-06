import subprocess

from aula_agent import store


def git(*args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def make_repo(tmp_path):
    main = tmp_path / "main"
    main.mkdir()
    git("init", "-q", cwd=main)
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "x", cwd=main)
    (main / "data").mkdir()
    (main / "data" / "state.json").write_text("{}")
    return main


def test_worktree_links_to_the_main_checkouts_data(tmp_path):
    main = make_repo(tmp_path)
    git("worktree", "add", "-q", str(tmp_path / "wt"), cwd=main)
    wt = tmp_path / "wt"

    data = store.default_data_dir(wt)
    assert data == wt / "data" and data.is_symlink()
    assert data.resolve() == (main / "data").resolve()
    assert (data / "state.json").exists()
    assert store.default_data_dir(wt) == wt / "data"  # second time: the link is used as is


def test_main_checkout_and_own_data_dir_are_left_alone(tmp_path):
    main = make_repo(tmp_path)
    assert store.default_data_dir(main) == main / "data"
    git("worktree", "add", "-q", str(tmp_path / "wt"), cwd=main)
    (tmp_path / "wt" / "data").mkdir()
    assert not store.default_data_dir(tmp_path / "wt").is_symlink()


def test_not_a_git_checkout(tmp_path):
    assert store.default_data_dir(tmp_path) == tmp_path / "data"
    assert not (tmp_path / "data").exists()


def test_the_real_data_dirs_are_the_checkouts_and_in_a_worktree_the_main_checkouts_too(tmp_path):
    main = make_repo(tmp_path)
    assert store.real_data_dirs(main) == [main / "data"]
    git("worktree", "add", "-q", str(tmp_path / "wt"), cwd=main)
    assert store.real_data_dirs(tmp_path / "wt") == [tmp_path / "wt" / "data", main.resolve() / "data"]
    assert store.real_data_dirs(tmp_path / "elsewhere") == [tmp_path / "elsewhere" / "data"]
