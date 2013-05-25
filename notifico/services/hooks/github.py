# -*- coding: utf8 -*-
__all__ = ('GithubHook',)

import re
import json
import requests

from flask.ext import wtf

from notifico.services.hooks import HookService


def simplify_payload(payload):
    """
    Massage the github webhook payload into something a little more
    usable. Idea comes from gith by danheberden.
    """
    result = {
        'branch': None,
        'tag': None,
        'pusher': None,
        'files': {
            'all': [],
            'added': [],
            'removed': [],
            'modified': []
        },
        'original': payload
    }

    # Try to find the branch/tag name from `ref`, falling back to `base_ref`.
    ref_r = re.compile(r'refs/(heads|tags)/(.*)$')
    for ref in (payload.get('ref', ''), payload.get('base_ref', '')):
        match = ref_r.match(ref)
        if match:
            type_, name = match.group(1, 2)
            result[{'heads': 'branch', 'tags': 'tag'}[type_]] = name
            break

    # Github (for whatever reason) doesn't always know the pusher. This field
    # is always missing/nil for commits generated by github itself, and for
    # web hooks coming from the "Test Hook" button.
    if 'pusher' in payload:
        result['pusher'] = payload['pusher'].get('name')

    # Summarize file movement over all the commits.
    for commit in payload.get('commits', tuple()):
        for type_ in ('added', 'removed', 'modified'):
            result['files'][type_].extend(commit[type_])
            result['files']['all'].extend(commit[type_])

    return result


class GithubConfigForm(wtf.Form):
    branches = wtf.TextField('Branches', validators=[
        wtf.Optional(),
        wtf.Length(max=1024)
    ], description=(
        'A comma-seperated list of branches to forward, or blank for all.'
        ' Ex: "master, dev"'
    ))
    use_colors = wtf.BooleanField('Use Colors', validators=[
        wtf.Optional()
    ], default=True, description=(
        'If checked, commit messages will include minor mIRC coloring.'
    ))
    show_branch = wtf.BooleanField('Show Branch Name', validators=[
        wtf.Optional()
    ], default=True, description=(
        'If checked, commit messages will include the branch name.'
    ))
    show_tags = wtf.BooleanField('Show Tags', validators=[
        wtf.Optional()
    ], default=True, description=(
        'If checked, changes to tags will be shown.'
    ))
    prefer_username = wtf.BooleanField('Prefer Usernames', validators=[
        wtf.Optional()
    ], default=True, description=(
        'If checked, show github usernames instead of commiter name when'
        ' possible.'
    ))
    full_project_name = wtf.BooleanField('Full Project Name', validators=[
        wtf.Optional()
    ], default=False, description=(
        'If checked, show the full github project name (ex: tktech/notifico)'
        ' instead of the Notifico project name (ex: notifico)'
    ))


class GithubHook(HookService):
    """
    HookService hook for http://github.com.
    """
    SERVICE_NAME = 'Github'
    SERVICE_ID = 10

    @classmethod
    def service_description(cls):
        return cls.env().get_template('github_desc.html').render()

    @classmethod
    def handle_request(cls, user, request, hook):
        p = request.form.get('payload', None)
        if not p:
            return

        j = simplify_payload(json.loads(p))
        original = j['original']

        # Config may not exist for pre-migrate hooks.
        config = hook.config or {}
        # Should we get rid of mIRC colors before sending?
        strip = not config.get('use_colors', True)
        # Branch names to filter on.
        branches = config.get('branches', None)
        # Display tag activity?
        show_tags = config.get('show_tags', True)

        if not original['commits']:
            if not show_tags or 'tag' not in j:
                # No commits, no tags, nothing to do.
                return
            yield cls.message(cls._create_tag_summary(j, config), strip=strip)
            return

        if branches:
            # The user wants to filter by branch name.
            branches = [b.strip().lower() for b in branches.split(',')]
            if j['branch'] and j['branch'].lower() not in branches:
                # This isn't a branch the user wants.
                return

        yield cls.message(cls._create_push_summary(j, config), strip=strip)
        for formatted_commit in cls._create_commit_summary(j, config):
            yield cls.message(formatted_commit, strip=strip)

    @classmethod
    def _create_tag_summary(cls, j, config):
        """
        Create and return a one-line summary of tag changes in `j`.
        """
        original = j['original']
        full_project_name = config.get('full_project_name', False)

        line = []

        # The name of the repository.
        project_name = original['repository']['name']
        if full_project_name:
            # The use wants the <username>/<project name> form from
            # github, not the Notifico name.
            project_name = '{username}/{project_Name}'.format(
                username=original['repository']['owner']['name'],
                project_Name=project_name
            )

        line.append(u'{RESET}[{BLUE}{name}{RESET}]'.format(
            name=project_name,
            **HookService.colors
        ))

        # The user doing the push, if available.
        if j['pusher']:
            line.append(u'{ORANGE}{pusher}{RESET} tagged'.format(
                pusher=j['pusher'],
                **HookService.colors
            ))
        else:
            line.append(u'Tagged')

        # The sha1 hash of the head (tagged) commit.
        line.append(u'{GREEN}{sha}{RESET} as'.format(
            sha=original['head_commit']['id'][:7],
            **HookService.colors
        ))

        # The tag itself.
        line.append(u'{GREEN}{tag}{RESET}'.format(
            tag=j['tag'],
            **HookService.colors
        ))

        # The shortened URL linking to the head (tagged) commit.
        line.append(u'{PINK}{link}{RESET}'.format(
            link=GithubHook.shorten(original['head_commit']['url']),
            **HookService.colors
        ))

        return u' '.join(line)

    @classmethod
    def _create_push_summary(cls, j, config):
        """
        Create and return a one-line summary of the push in `j`.
        """
        original = j['original']
        show_branch = config.get('show_branch', True)
        full_project_name = config.get('full_project_name', False)

        # Build the push summary.
        line = []

        # The name of the repository.
        project_name = original['repository']['name']
        if full_project_name:
            # The use wants the <username>/<project name> form from
            # github, not the Notifico name.
            project_name = '{username}/{project_Name}'.format(
                username=original['repository']['owner']['name'],
                project_Name=project_name
            )

        line.append(u'{RESET}[{BLUE}{name}{RESET}]'.format(
            name=project_name,
            **HookService.colors
        ))

        # The user doing the push, if available.
        if j['pusher']:
            line.append(u'{ORANGE}{pusher}{RESET} pushed'.format(
                pusher=j['pusher'],
                **HookService.colors
            ))

        # The number of commits included in this push.
        line.append(u'{GREEN}{count}{RESET} {commits}'.format(
            count=len(original['commits']),
            commits='commit' if len(original['commits']) == 1 else 'commits',
            **HookService.colors
        ))

        if show_branch and j['branch']:
            line.append(u'to {GREEN}{branch}{RESET}'.format(
                branch=j['branch'],
                **HookService.colors
            ))

        # File movement summary.
        line.append(u'[+{added}/-{removed}/\u00B1{modified}]'.format(
            added=len(j['files']['added']),
            removed=len(j['files']['removed']),
            modified=len(j['files']['modified'])
        ))

        # The shortened URL linking to the compare page.
        line.append(u'{PINK}{compare_link}{RESET}'.format(
            compare_link=GithubHook.shorten(original['compare']),
            **HookService.colors
        ))

        return u' '.join(line)

    @classmethod
    def _create_commit_summary(cls, j, config):
        """
        Create and yield a one-line summary of each commit in `j`.
        """
        prefer_username = config.get('prefer_username', True)
        full_project_name = config.get('full_project_name', False)

        original = j['original']

        for commit in original['commits']:
            committer = commit.get('committer', {})
            author = commit.get('author', {})

            line = []

            # The name of the repository.
            project_name = original['repository']['name']
            if full_project_name:
                # The use wants the <username>/<project name> form from
                # github, not the Notifico name.
                project_name = '{username}/{project_Name}'.format(
                    username=original['repository']['owner']['name'],
                    project_Name=project_name
                )

            line.append(u'{RESET}[{BLUE}{name}{RESET}]'.format(
                name=project_name,
                **HookService.colors
            ))

            # Show the committer.
            attribute_to = None
            if prefer_username:
                attribute_to = author.get('username')
                if attribute_to is None:
                    attribute_to = author.get('username')

            if attribute_to is None:
                attribute_to = author.get('name')
                if attribute_to is None:
                    attribute_to = committer.get('name')

            if attribute_to:
                line.append(u'{ORANGE}{attribute_to}{RESET}'.format(
                    attribute_to=attribute_to,
                    **HookService.colors
                ))

            line.append(u'{GREEN}{sha}{RESET}'.format(
                sha=commit['id'][:7],
                **HookService.colors
            ))

            line.append(u'-')
            line.append(commit['message'])

            yield u' '.join(line)

    @classmethod
    def shorten(cls, url):
        # Make sure the URL hasn't already been shortened, since github
        # may does this in the future for web hooks. Better safe than silly.
        if re.search(r'^https?://git.io', url):
            return url

        # Only github URLs can be shortened by the git.io service, which
        # will return a 201 created on success and return the new url
        # in the Location header.
        try:
            r = requests.post('http://git.io', data={
                'url': url
            }, timeout=4.0)
        except requests.exceptions.Timeout:
            return url

        # Something went wrong, usually means we're being throttled.
        # TODO: If we are being throttled, handle this smarter instead
        #       of trying again on the next message.
        if r.status_code != 201:
            return url

        return r.headers['Location']

    @classmethod
    def form(cls):
        return GithubConfigForm
