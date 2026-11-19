#!/usr/bin/env python3
"""Read-only GitHub release preflight. No AWS credentials or write requests."""
import json
import os
import re
import sys
from urllib.request import Request, urlopen

REPOSITORY = 'Flem15/aws-llm-production-lab'
REQUIRED_CHECKS = {'Application and Docker', 'CDK Build and Synthesis',
                   'Restricted Egress Security and Docker', 'Bot City Safety and Docker',
                   'Release Image Security'}


def validate(context, environment, branches, protection, main):
    errors = []
    if (context.get('repository') != REPOSITORY or context.get('ref') != 'refs/heads/main'
            or context.get('event') != 'workflow_dispatch'):
        errors.append('release requires the canonical repository and main branch')
    if context.get('actor') != 'Flem15' or context.get('actor_id') != '127445894' or context.get('triggering_actor') != 'Flem15':
        errors.append('release must be manually initiated by the selected maintainer')
    if context.get('action') not in {'publication','deployment','evidence'} or context.get('confirmation') != 'development-'+str(context.get('action')):
        errors.append('explicit development action confirmation required')
    sha = context.get('sha', '')
    if not re.fullmatch('[0-9a-f]{40}', sha) or sha != main.get('sha'):
        errors.append('workflow commit must equal current main HEAD')
    if context.get('image_tag', sha) != sha:
        errors.append('requested image must match the approved workflow commit')
    if context.get('region') != 'us-east-2' or context.get('role') != 'arn:aws:iam::655469962246:role/llm-parity-actions-ecr-push':
        errors.append('development region or role mismatch')
    if environment.get('can_admins_bypass') is not False:
        errors.append('environment admin bypass must be disabled')
    if environment.get('deployment_branch_policy') != {'protected_branches': False, 'custom_branch_policies': True}:
        errors.append('explicit main-only environment branch policy required')
    if branches.get('total_count') != 1 or [(r.get('name'),r.get('type')) for r in branches.get('branch_policies',[])] != [('main','branch')]:
        errors.append('environment must allow only the main branch, no tags')
    reviews = protection.get('required_pull_request_reviews') or {}
    if (reviews.get('required_approving_review_count',0) != 0
            or reviews.get('require_code_owner_reviews',False)
            or reviews.get('require_last_push_approval',False)):
        errors.append('solo-maintainer policy must not require independent approvals')
    if any(reviews.get('bypass_pull_request_allowances',{}).get(k) for k in ['users','teams','apps']):
        errors.append('pull request bypass actors are forbidden')
    if protection.get('enforce_admins',{}).get('enabled') is not True:
        errors.append('main protections must apply to administrators')
    for key in ['allow_force_pushes','allow_deletions']:
        if protection.get(key,{}).get('enabled') is not False:
            errors.append(key+' must be disabled')
    checks = protection.get('required_status_checks') or {}
    bound = {c.get('context') for c in checks.get('checks',[]) if c.get('app_id') == 15368}
    if checks.get('strict') is not True or not REQUIRED_CHECKS.issubset(bound):
        errors.append('required regression checks and up-to-date branch are missing')
    return errors


def fetch(path):
    # Never follow redirects with a credential-bearing request.
    from urllib.request import HTTPRedirectHandler, build_opener
    class NoRedirect(HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None
    token = os.environ['GH_TOKEN']
    request = Request('https://api.github.com/repos/'+REPOSITORY+'/'+path,
        headers={'Authorization':'Bearer '+token,'Accept':'application/vnd.github+json','X-GitHub-Api-Version':'2022-11-28'})
    with build_opener(NoRedirect).open(request, timeout=20) as response:
        return json.load(response)


def main():
    try:
        context = dict(repository=os.environ.get('GITHUB_REPOSITORY'),ref=os.environ.get('GITHUB_REF'),
            event=os.environ.get('GITHUB_EVENT_NAME'),sha=os.environ.get('GITHUB_SHA'),
            region=os.environ.get('RELEASE_REGION'),role=os.environ.get('RELEASE_ROLE'),
            actor=os.environ.get('GITHUB_ACTOR'),actor_id=os.environ.get('GITHUB_ACTOR_ID'),triggering_actor=os.environ.get('GITHUB_TRIGGERING_ACTOR'),
            action=os.environ.get('RELEASE_ACTION'),confirmation=os.environ.get('RELEASE_CONFIRMATION'))
        if os.environ.get('RELEASE_IMAGE_TAG'):context['image_tag']=os.environ['RELEASE_IMAGE_TAG']
        errors = validate(context,fetch('environments/dev'),fetch('environments/dev/deployment-branch-policies?per_page=100'),
                          fetch('branches/main/protection'),fetch('commits/main'))
    except Exception:
        print('NO-GO: GitHub protection evidence unavailable or malformed; credentials withheld.')
        return 2
    for error in errors:print('NO-GO: '+error)
    if not errors:print('GitHub policy preflight passed; solo-maintainer confirmation is not independent approval; evidence gate still required.')
    return 2 if errors else 0

if __name__ == '__main__':sys.exit(main())
