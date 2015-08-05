#!/usr/bin/python

# Copyright (c) 2015 SUSE LINUX GmbH, Nuernberg, Germany.
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License along
# with this program; if not, write to the Free Software Foundation, Inc.,
# 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.

import argparse
import functools
import os
import shutil
import sys
import tempfile

import sh
from sh import Command

IBS_MAPPING = {
    'release/stoney/master': 'Devel:Cloud:4:Staging',
    'release/tex/master':    'Devel:Cloud:5:Staging',
    'master':                'Devel:Cloud:6:Staging'
}

CLOUDSRC = {
    'release/stoney/master': 'develcloud4',
    'release/tex/master':    'develcloud5',
    'master':                'develcloud6'
}

JOB_PARAMETERS = {
    'barclamp-ceph': ('nodenumber=3', 'networkingplugin=linuxbridge'),
    'barclamp-pacemaker': ('nodenumber=3', 'hacloud=1')
}

htdocs_dir = '/srv/www/htdocs/mkcloud'
htdocs_url = 'http://tu-sle12.j.cloud.suse.de/mkcloud/'

iosc = functools.partial(
    Command('/usr/bin/osc'), '-A', 'https://api.suse.de')


def ghs_set_status(org, repo, pr_id, head_sha1, status):
    ghs = Command(
        os.path.abspath(
            os.path.join(os.path.dirname(sys.argv[0]),
                         'github-status/github-status.rb')))

    ghs('-r', org + '/' + repo,
        '-p', pr_id, '-c', head_sha1, '-a', 'set-status',
        '-s', status)


def jenkins_job_trigger(org, repo, pr_id, sha1, cloudsource, ptfdir):
    print("triggering jenkins job with " + htdocs_url + ptfdir)

    jenkins = Command(
        os.path.abspath(
            os.path.join(os.path.dirname(sys.argv[0]),
                         'jenkins/jenkins-job-trigger')))

    job_parameters = (
        'nodenumber=2', 'networkingplugin=openvswitch')

    if repo in JOB_PARAMETERS:
        job_parameters = JOB_PARAMETERS[repo]

    job_parameters += ('all_noreboot',)

    print(jenkins(
        'openstack-mkcloud',
        '-p', 'mode=standard',
        "github_pr=%s/%s:%s:%s" % (org, repo, pr_id, sha1),
        "cloudsource=" + cloudsource,
        'label=openstack-mkcloud-SLE12',
        'UPDATEREPOS=' + htdocs_url + ptfdir,
        'mkcloudtarget=all_noreboot',
        *job_parameters))


def add_pr_to_checkout(org, repo, pr_id, spec):
    sh.curl(
        '-s', '-k', '-L',
        "https://github.com/%s/%s/pull/%s.patch" % (org, repo, pr_id),
        '-o', 'prtest.patch')
    sh.sed('-i', '-e', 's,Url:.*,%define _default_patch_fuzz 2,',
           '-e', 's,%patch[0-36-9].*,,', spec)
    Command('/usr/lib/build/spec_add_patch')(spec, 'prtest.patch')
    iosc('vc', '-m', " added PR test patch from %s/%s" % (repo, pr_id))


def prep_osc_dir(workdir, org, repo, pr_id, pr_branch, pkg, spec):
    os.chdir(workdir)
    iosc('co', IBS_MAPPING[pr_branch], pkg)
    os.chdir(os.path.join(IBS_MAPPING[pr_branch], pkg))
    add_pr_to_checkout(org, repo, pr_id, spec)


def build_package(spec, webroot, olddir):
    buildroot = os.path.join(os.getcwd(), 'BUILD')

    try:
        iosc('build', '--root', buildroot,
             '--noverify', '--noservice', 'SLE_11_SP3', 'x86_64',
             spec, _out=sys.stdout)
    except:
        build_failed = True
        print("Build failed: " + str(sys.exc_info()[0]))
        raise
    else:
        sh.cp('-p',
              sh.glob(os.path.join(buildroot,
                                   'usr/src/packages/RPMS/*/*.rpm')),
              webroot)
    finally:
        os.chdir(olddir)
        log = os.path.join(buildroot, '.build.log')
        if os.path.exists(log):
            shutil.copy2(log, os.path.join(webroot, 'build.log'))


def trigger_testbuild(args):
    olddir = os.getcwd()
    workdir = tempfile.mkdtemp()
    build_failed = False
    try:
        ptfdir = ':'.join([args.repo, args.pr_id, args.sha1, args.branch])
        webroot = os.path.join(htdocs_dir, ptfdir)
        pkg = args.repo if args.repo == "crowbar" else "crowbar-" + args.repo
        spec = pkg + '.spec'

        shutil.rmtree('-rf', webroot)
        if not os.path.isdir(webroot):
            os.makedirs(webroot)

        prep_osc_dir(workdir, args.org, args.repo, args.pr_id, args.branch,
                     pkg, spec)
        build_package(spec, webroot, olddir)
    finally:
        sh.sudo.rm('-rf', workdir)

    if not build_failed:
        jenkins_job_trigger(
            args.org, args.repo, args.pr_id, args.sha1,
            CLOUDSRC[args.branch], ptfdir)

    ghs_set_status(
        args.org, args.repo, args.pr_id, args.sha1,
        'failure' if build_failed else'pending')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Test a github pull request')
    parser.add_argument("org",    metavar='ORG',
                        help='github organization name')
    parser.add_argument("repo",   metavar='REPO',
                        help='github repository name')
    parser.add_argument('pr_id',  metavar='PR-ID',
                        help='github PR id')
    parser.add_argument('sha1',   metavar='SHA1',
                        help='SHA1 head of PR')
    parser.add_argument('branch', metavar='BRANCH',
                        help='destination branch of PR')

    args = parser.parse_args()

    trigger_testbuild(args)
    sys.exit(0)
