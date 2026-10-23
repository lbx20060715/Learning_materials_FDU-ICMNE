"""Upload local course folders to a new private GitHub repository.

Run: python upload_to_github.py
Requires Git and Git LFS; authenticate using GH_TOKEN/GITHUB_TOKEN or Git's
credential manager. Original course files are never changed.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import urllib.error
import urllib.request

SUBJECTS = ['程序设计', '大学物理B（上）', '大学物理B（下）', '电路基础',
            '高等数学A（上）', '高等数学A（下）', '基础物理实验', '线性代数']

def run(args, cwd=None, capture=False):
    return subprocess.run(args, cwd=cwd, check=True, text=True,
                          encoding='utf-8', stdout=subprocess.PIPE if capture else None)

def token():
    value = os.environ.get('GH_TOKEN') or os.environ.get('GITHUB_TOKEN')
    if value:
        return value
    env = dict(os.environ, GIT_TERMINAL_PROMPT='0', GCM_INTERACTIVE='Never')
    try:
        result = subprocess.run(['git', 'credential', 'fill'],
            input='protocol=https\nhost=github.com\nusername=lbx20060715\n\n', capture_output=True,
            text=True, timeout=20, env=env)
        fields = dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)
        if result.returncode == 0 and fields.get('password'):
            return fields['password']
        diagnostic = result.stderr.lower()
        if 'wincredman' in diagnostic:
            raise RuntimeError('Windows Credential Manager could not access the saved credential. In your own PowerShell run: git credential-manager github login --username lbx20060715 --browser --force ; then retry this script.')
    except subprocess.TimeoutExpired:
        pass
    raise RuntimeError('No local GitHub credential. Log in with Git Credential Manager or set GH_TOKEN, then run again.')

def api(path, secret, data=None):
    req = urllib.request.Request('https://api.github.com' + path,
        data=json.dumps(data).encode() if data is not None else None,
        headers={'Authorization': 'Bearer ' + secret, 'Accept': 'application/vnd.github+json',
                 'User-Agent': 'course-materials-uploader'})
    with urllib.request.urlopen(req, timeout=60) as response:
        return json.load(response)

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument('--work', type=Path, default=Path('D:/workspace/course-materials-upload'))
    parser.add_argument('--repo', default='course-materials')
    parser.add_argument('--check-auth', action='store_true')
    args = parser.parse_args()
    secret = token()
    user = api('/user', secret)['login']
    if user.lower() != 'lbx20060715':
        raise RuntimeError('Authenticated account is not lbx20060715; stopped.')
    print('Authenticated:', user)
    if args.check_auth:
        return
    source, work = args.source.resolve(), args.work.resolve()
    if source == work or source in work.parents:
        raise RuntimeError('Work directory must be outside the source folder.')
    missing = [s for s in SUBJECTS if not (source / s).is_dir()]
    if missing:
        raise RuntimeError('Missing subject folders: ' + ', '.join(missing))
    run(['git', 'lfs', 'version'])
    full = user + '/' + args.repo
    try:
        repo = api('/repos/' + full, secret)
        if not repo['private']:
            raise RuntimeError('Existing repository is public; stopped to avoid publishing unintentionally.')
    except urllib.error.HTTPError as error:
        if error.code != 404:
            raise
        repo = api('/user/repos', secret, {'name': args.repo, 'private': True,
            'description': '按科目分类的课程学习资料', 'auto_init': False})
    url = repo['html_url']
    if not (work / '.git').exists():
        if work.exists() and any(work.iterdir()):
            raise RuntimeError('Work directory is not empty; choose another --work path.')
        work.mkdir(parents=True, exist_ok=True)
        if repo.get('size', 0) or repo.get('default_branch') and repo.get('pushed_at'):
            run(['git', 'clone', url + '.git', str(work)])
        else:
            run(['git', 'init', '-b', 'main', str(work)])
            run(['git', 'remote', 'add', 'origin', url + '.git'], work)
    remote = run(['git', 'remote', 'get-url', 'origin'], work, True).stdout.strip()
    if remote.rstrip('/') != url + '.git':
        raise RuntimeError('Work directory belongs to another repository.')
    run(['git', 'config', 'user.name', user], work)
    run(['git', 'config', 'user.email', str(repo['owner']['id']) + '+' + user + '@users.noreply.github.com'], work)
    run(['git', 'lfs', 'install', '--local'], work)
    # Supply the credential only through the child process environment.
    os.environ['GIT_CONFIG_COUNT'] = '1'
    os.environ['GIT_CONFIG_KEY_0'] = 'credential.helper'
    os.environ['GIT_CONFIG_VALUE_0'] = '!python -c "import os; print(\'username=x-access-token\'); print(\'password=\'+os.environ[\'COURSE_UPLOAD_TOKEN\'])"'
    os.environ['COURSE_UPLOAD_TOKEN'] = secret
    os.environ['GIT_TERMINAL_PROMPT'] = '0'
    attrs = ['# Files over 100 MiB are stored with Git LFS.']
    all_files = []
    for subject in SUBJECTS:
        files = sorted((source / subject).rglob('*'))
        for item in files:
            if not item.is_file() or item.name.startswith('~$'):
                continue
            relative = item.relative_to(source)
            if any(p in {'.git', '.aws', '.codex', '.agents'} for p in relative.parts):
                continue
            all_files.append((item, relative))
            if item.stat().st_size >= 100 * 1024 * 1024:
                pattern = '/' + relative.as_posix()
                attrs.append(json.dumps(pattern, ensure_ascii=False) + ' filter=lfs diff=lfs merge=lfs -text')
    (work / '.gitattributes').write_text('\n'.join(attrs) + '\n', encoding='utf-8')
    (work / 'README.md').write_text('# 课程学习资料\n\n按科目保留原有目录结构。\n\n' +
        '\n'.join('- ' + subject for subject in SUBJECTS) +
        '\n\n大文件使用 Git LFS；完整下载请安装 Git LFS 后运行 `git lfs pull`。\n', encoding='utf-8')
    shutil.copy2(Path(__file__), work / 'upload_to_github.py')
    def commit_push(message):
        run(['git', 'add', '--all'], work)
        changed = subprocess.run(['git', 'diff', '--cached', '--quiet'], cwd=work).returncode
        if changed not in (0, 1):
            raise RuntimeError('Cannot inspect staged changes.')
        if changed == 1:
            run(['git', 'commit', '-m', message], work)
        # Always retry the push, including after an earlier interrupted push.
        run(['git', 'push', '-u', 'origin', 'HEAD'], work)
    commit_push('Add course index and upload script')
    for subject in SUBJECTS:
        batch_size = 0
        for item, relative in all_files:
            if relative.parts[0] != subject:
                continue
            dest = work / relative
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, dest)
            batch_size += item.stat().st_size
            if batch_size >= 150 * 1024 * 1024:
                commit_push('Upload ' + subject)
                batch_size = 0
        commit_push('Upload ' + subject)
    local = run(['git', 'rev-parse', 'HEAD'], work, True).stdout.strip()
    remote_head = run(['git', 'ls-remote', 'origin', 'HEAD'], work, True).stdout.split()[0]
    if local != remote_head:
        raise RuntimeError('Remote verification failed.')
    print('Upload verified:', url)
    print('Files:', len(all_files))

if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print('Stopped:', str(error), file=sys.stderr)
        sys.exit(1)
