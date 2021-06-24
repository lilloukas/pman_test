
import os
import logging
import json
import platform
import multiprocessing
import socket

from flask import current_app as app
from flask_restful import reqparse, abort, Resource

from .abstractmgr import ManagerException
from .openshiftmgr import OpenShiftManager
from .kubernetesmgr import KubernetesManager
from .swarmmgr import SwarmManager


logger = logging.getLogger(__name__)

parser = reqparse.RequestParser(bundle_errors=True)
parser.add_argument('jid', dest='jid', required=True)
parser.add_argument('cmd_args', dest='cmd_args', required=True)
parser.add_argument('cmd_path_flags', dest='cmd_path_flags', required=True)
parser.add_argument('auid', dest='auid', required=True)
parser.add_argument('number_of_workers', dest='number_of_workers', required=True)
parser.add_argument('cpu_limit', dest='cpu_limit', required=True)
parser.add_argument('memory_limit', dest='memory_limit', required=True)
parser.add_argument('gpu_limit', dest='gpu_limit', required=True)
parser.add_argument('image', dest='image', required=True)
parser.add_argument('selfexec', dest='selfexec', required=True)
parser.add_argument('selfpath', dest='selfpath', required=True)
parser.add_argument('execshell', dest='execshell', required=True)
parser.add_argument('type', dest='type', choices=('ds', 'fs', 'ts'), required=True)




def get_compute_mgr(container_env):
    compute_mgr = None
    if container_env == 'swarm':
        compute_mgr = SwarmManager(app.config)
    elif container_env == 'kubernetes':
        compute_mgr = KubernetesManager(app.config)
    elif container_env == 'openshift':
        compute_mgr = OpenShiftManager()
    return compute_mgr


class JobListResource(Resource):
    """
    Resource representing the list of jobs scheduled on the compute.
    """

    def __init__(self):
        super(JobListResource, self).__init__()

        # mounting points for the input and outputdir in the app's container!
        self.str_app_container_inputdir = '/share/incoming'
        self.str_app_container_outputdir = '/share/outgoing'

        self.container_env = app.config.get('CONTAINER_ENV')
        self.openshiftmgr       = None

    def get(self):
        return {
            'server_version': app.config.get('SERVER_VERSION')
        }
        
    def get_openshift_manager(self):
        self.openshiftmgr = OpenShiftManager()
        return self.openshiftmgr
        

    def post(self):
        args = parser.parse_args()

        job_id = args.jid.lstrip('/')
                

        cmd = self.build_app_cmd(args.cmd_args, args.cmd_path_flags, args.selfpath,
                                 args.selfexec, args.execshell, args.type)

        resources_dict = {'number_of_workers': args.number_of_workers,
                          'cpu_limit': args.cpu_limit,
                          'memory_limit': args.memory_limit,
                          'gpu_limit': args.gpu_limit,
                          }
        share_dir = None
        storage_type = app.config.get('STORAGE_TYPE')
        if storage_type in ('host', 'nfs'):
            storebase = app.config.get('STOREBASE')
            share_dir = os.path.join(storebase, 'key-' + job_id)


        logger.info(f'Scheduling job {job_id} on the {self.container_env} cluster')

        compute_mgr = get_compute_mgr(self.container_env)
        try:
            job = compute_mgr.schedule_job(args.image, cmd, job_id, resources_dict,
                                           share_dir)
        except ManagerException as e:
            logger.error(f'Error from {self.container_env} while scheduling job '
                         f'{job_id}, detail: {str(e)}')
            abort(e.status_code, message=str(e))

        job_info = compute_mgr.get_job_info(job)
        logger.info(f'Successful job {job_id} schedule response from '
                    f'{self.container_env}: {job_info}')
        job_logs = compute_mgr.get_job_logs(job)
           
            
        


        return {
            'jid': job_id,
            'image': job_info['image'],
            'cmd': job_info['cmd'],
            'status': job_info['status'],
            'message': job_info['message'],
            'timestamp': job_info['timestamp'],
            'logs': job_logs
        },201

    def build_app_cmd(self, cmd_args, cmd_path_flags, selfpath, selfexec, execshell,
                      plugin_type):
        """
        Build and return the app's cmd string.
        """
        if cmd_path_flags:
            # process the argument of any cmd flag that is a 'path'
            path_flags = cmd_path_flags.split(',')
            args = cmd_args.split()
            for i in range(len(args) - 1):
                if args[i] in path_flags:
                    # each flag value is a string of one or more paths separated by comma
                    # paths = args[i+1].split(',')
                    # base_inputdir = self.str_app_container_inputdir
                    # paths = [os.path.join(base_inputdir, p.lstrip('/')) for p in paths]
                    # args[i+1] = ','.join(paths)

                    # the next is tmp until CUBE's assumptions about inputdir and path
                    # parameters are removed
                    args[i+1] = self.str_app_container_inputdir
            cmd_args = ' '.join(args)
        outputdir = self.str_app_container_outputdir
        exec = os.path.join(selfpath, selfexec)
        cmd = f'{execshell} {exec}'
        if plugin_type == 'ds':
            inputdir = self.str_app_container_inputdir
            cmd = cmd + f' {cmd_args} {inputdir} {outputdir}'
        elif plugin_type in ('fs', 'ts'):
            cmd = cmd + f' {cmd_args} {outputdir}'
        return cmd


class JobResource(Resource):
    """
    Resource representing a single job scheduled on the compute.
    """

    def __init__(self):
        super(JobResource, self).__init__()

        self.container_env = app.config.get('CONTAINER_ENV')
        self.compute_mgr = get_compute_mgr(self.container_env)

    
    # Initiate an openshiftmgr instance
    def get_openshift_manager(self):
        self.openshiftmgr = OpenShiftManager()
        return self.openshiftmgr
        
            
    def get(self, job_id):
    
        job_id = job_id.lstrip('/')

        logger.info(f'Getting job {job_id} status from the {self.container_env} '
                    f'cluster')
        
        try:
            job = self.compute_mgr.get_job(job_id)
        except ManagerException as e:
            abort(e.status_code, message=str(e))
        job_info = self.compute_mgr.get_job_info(job)
        logger.info(f'Successful job {job_id} status response from '
                    f'{self.container_env}: {job_info}')
        job_logs = self.compute_mgr.get_job_logs(job)
        
        return {
            'jid': job_id,
            'image': job_info['image'],
            'cmd': job_info['cmd'],
            'status': job_info['status'],
            'message': job_info['message'],
            'timestamp': job_info['timestamp'],
            'logs': job_logs
                }


    def delete(self, job_id):
        job_id = job_id.lstrip('/')

        logger.info(f'Deleting job {job_id} from {self.container_env}')
        try:
            job = self.compute_mgr.get_job(job_id)
        except ManagerException as e:
            abort(e.status_code, message=str(e))
        self.compute_mgr.remove_job(job)  # remove job from compute cluster
        logger.info(f'Successfully removed job {job_id} from {self.container_env}')
        return '', 204

        
class Hello(Resource):

     # Respond to simple 'hello' requests from the server
    def get(self):
   
            container_env = app.config.get('CONTAINER_ENV')

            b_status            = False
            d_ret               = {}
            d_ret['message']                = (f'pman says hello from {container_env} ')
            d_ret['sysinfo']                = {}
            d_ret['sysinfo']['system']      = platform.system()
            d_ret['sysinfo']['machine']     = platform.machine()
            d_ret['sysinfo']['platform']    = platform.platform()
            d_ret['sysinfo']['uname']       = platform.uname()
            d_ret['sysinfo']['version']     = platform.version()
            d_ret['sysinfo']['cpucount']    = multiprocessing.cpu_count()
            d_ret['sysinfo']['loadavg']     = os.getloadavg()
            d_ret['sysinfo']['hostname']    = socket.gethostname()
            d_ret['sysinfo']['inet']        = [l for l in ([ip for ip in socket.gethostbyname_ex(socket.gethostname())[2] if not ip.startswith("127.")][:1], [[(s.connect(('8.8.8.8', 53)), s.getsockname()[0], s.close()) for s in [socket.socket(socket.AF_INET, socket.SOCK_DGRAM)]][0][1]]) if l][0][0]
            b_status                        = True
            
            return { 'd_ret':   d_ret,
                 'status':  b_status}
                 

