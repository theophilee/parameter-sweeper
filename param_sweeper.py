import os

'''
A script for generating SLURM submission scripts which sweep parameters
from https://gist.github.com/TysonRayJones/1c4cae5acd7fde3a90da743cbb79db2e
'''


# SLURM fields assumed if the particular field isn't passed to get_script
# can contain unused fields

DEFAULT_SLURM_FIELDS = {
    'time_h': 0, 'time_m': 0, 'time_s': 0,
    'job_name': 'myjob',
    'output': 'output.txt',
}



# a template for the entire submit script
# (bash braces must be escaped by doubling: $var = ${{var}})
# num_gpus, num_jobs, param_arr_init, param_val_assign and param_list special fields
   
TEMPLATE = '''
#!/bin/env bash
#SBATCH -p GPU-AI
#SBATCH -N 1
#SBATCH --gres=gpu:volta16:1
#SBATCH --array=0-{num_jobs}
#SBATCH --job-name={job_name}
#SBATCH --output=%A_%a.out
#SBATCH --time={time_h}:{time_m}:{time_s}

{param_arr_init}

trial=${{SLURM_ARRAY_TASK_ID}}

{param_val_assign}

sleep $[$RANDOM % 120]s
cp $HOME/.mujoco/licenses/`hostname`/mjkey.txt $HOME/.mujoco/mjkey.txt
cd $SCRATCH/handful-of-trials-pytorch

{command}
'''.strip()



# functions for making bash expressions
# bash braces are escaped by doubling

def _mth(exp):
    return '$(( %s ))' % exp
def _len(arr):
    return '${{#%s[@]}}' % arr
def _get(arr, elem):
    return '${{%s[%s]}}' % (arr, elem)
def _eq(var, val):
    return '%s=%s' % (var, val)
def _op(a, op, b):
    return _mth('%s %s %s' % (a, op, b))
def _arr(arr):
    return '( %s )' % ' '.join(map(str, arr))
def _seq(a, b, step):
    return '($( seq %d %d %d ))' % (a, step, b)
def _var(var):
    return '${%s}' % var



# templates for param array construction and element access

PARAM_ARR = '{param}_values'
PARAM_EXPRS = {
    'param_arr_init':
        _eq(PARAM_ARR, '{values}'),
    'param_val_assign': {
        'assign':
            _eq('{param}', _get(PARAM_ARR, _op('trial','%',_len(PARAM_ARR)))),
        'increment':
            _eq('trial', _op('trial', '/', _len(PARAM_ARR)))
    }
}



def _to_bash(obj):
    if isinstance(obj, range):
        return _seq(obj.start, obj.stop - 1, obj.step)
    if isinstance(obj, list) or isinstance(obj, tuple):
        return _arr(obj)
    raise ValueError('Unknown object type %s' % type(obj).__name__)



def _get_params_bash(params, values):
    # builds bash code to perform the equivalent of
    '''
    def get_inds(params, ind):
        inds = []
        for length in map(len, params):
            inds.append(ind % length)
            ind //= length
        return inds[::-1]
    '''

    # get lines of bash code for creating/accessing param arrays
    init_lines = []
    assign_lines = []
    init_temp = PARAM_EXPRS['param_arr_init']
    assign_temps = PARAM_EXPRS['param_val_assign']

    for param, vals in zip(params, values):
        init_lines.append(
            init_temp.format(param=param, values=_to_bash(vals)))
        assign_lines.append(
            assign_temps['assign'].format(param=param))
        assign_lines.append(
            assign_temps['increment'].format(param=param))

    # remove superfluous final trial reassign
    assign_lines.pop()

    return init_lines, assign_lines



def _get_command_bash(params):
    cmd = 'echo ' + ' '.join([f'${p}' for p in params])
    cmd += '\npython main.py ' + ' '.join([f'--{p} ${p}' for p in params])
    return cmd
    
    

def get_script(fields, params, param_order=None):
    '''
    returns a string of a SLURM submission script using the passed fields
    and which creates an array of jobs which sweep the given params
    fields:      dict of SLURM field names to their values. type is ignored
    params:      a dict of (param names, param value list) pairs.
                 The param name is the name of the bash variable created in
                 the submission script which will contain the param's current
                 value (for that SLURM job instance). param value list is
                 a list (or range instance) of the values the param should take,
                 to be run once against every other possible configuration of all params.
    param_order: a list containing all param names which indicates the ordering
                 of the params in the sweep. The last param changes every
                 job number. If not supplied, uses an arbitrary order
    '''

    # check arguments have correct type
    assert isinstance(fields, dict)
    assert isinstance(params, dict)
    assert (isinstance(param_order, list) or
            isinstance(param_order, tuple) or
            param_order==None)
    if param_order == None:
        param_order = list(params.keys())

    # check each field appears in the template
    for field in fields:
        if ('{%s}' % field) not in TEMPLATE:
            raise ValueError('passed field %s unused in template' % field)

    # calculate total number of gpus
    num_gpus = 1
    for vals in params.values():
        num_gpus *= len(vals)

    # get bash code for param sweeping
    init_lines, assign_lines = _get_params_bash(
        param_order, [params[key] for key in param_order])

    # build template substitutions (overriding defaults)
    subs = {
        'param_arr_init': '\n'.join(init_lines),
        'param_val_assign': '\n'.join(assign_lines),
        'param_list': ', '.join(map(_var, param_order)),
        'command': _get_command_bash(param_order),
        'num_jobs': num_gpus - 1,
        'num_gpus': num_gpus
    }
    for key, val in DEFAULT_SLURM_FIELDS.items():
        subs[key] = val
    for key, val in fields.items():
        subs[key] = val
        
    return TEMPLATE.format(**subs)


def save_script(filename, fields, params, param_order=None):
    '''
    creates and writes to file a SLURM submission script using the passed
    fields and which creates an array of jobs which sweep the given params
    fields:      dict of SLURM field names to their values. type is ignored
    params:      a dict of (param names, param value list) pairs.
                 The param name is the name of the bash variable created in
                 the submission script which will contain the param's current
                 value (for that SLURM job instance). param value list is
                 a list (or range instance) of the values the param should take,
                 to be run once against every other possible configuration of all params.
    param_order: a list containing all param names which indicates the ordering
                 of the params in the sweep. The last param changes every
                 job number. If not supplied, uses an arbitrary order
    '''
    
    script_str = get_script(fields, params, param_order)
    if ('/' in filename) or ('\\' in filename):
        os.makedirs(os.path.dirname(filename), exist_ok=True)
    with open(filename, 'w') as file:
        file.write(script_str)



if __name__ == '__main__':
    
    fields = {
        'job_name': 'dpi-global',
        'time_h': 48,
    }
   
    params = {
        'env': ['half_cheetah'],
	'stochasticity': ['gaussian_bias'],
	'ensemble_size': [5],
	'hid_features': ['200,200', '200,200,200,200'],
	'seed': [5, 6],
	'weight_decay': [1e-4],
	'expert_demos': ['true', 'false']
    }
 	
    save_script('run_experiments.slurm', fields, params)
