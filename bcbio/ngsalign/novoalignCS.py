"""Next-gen sequencing alignment with novoalignCS: http://www.novocraft.com

For BAM input handling this requires:
  novoalignCS (with license for multicore)
  novosort (also with license for multicore)
  samtools
"""
import os
import subprocess

from bcbio import utils
from bcbio.distributed.transaction import file_transaction
from bcbio.ngsalign import alignprep
from bcbio.pipeline import config_utils
from bcbio.provenance import do
from bcbio.utils import (memoize_outfile, file_exists, curdir_tmpdir)

# ## BAM realignment

def get_rg_info(names):
    return r"@RG\tID:{rg}\tPL:{pl}\tPU:{pu}\tSM:{sample}".format(**names)

def align_bam(in_bam, ref_file, names, align_dir, config):
    """Perform realignment of input BAM file, handling sorting of input/output with novosort.

    Uses unix pipes for avoid IO writing between steps:
      - novosort of input BAM to coordinates
      - alignment with novoalignCS
      - conversion to BAM with samtools
      - coordinate sorting with novosort
    """
    out_file = os.path.join(align_dir, "{0}-sort.bam".format(names["lane"]))
    novosort = config_utils.get_program("novosort", config)
    novoalignCS = config_utils.get_program("novoalignCS", config)
    samtools = config_utils.get_program("samtools", config)
    resources = config_utils.get_resources("novoalignCS", config)
    num_cores = config["algorithm"].get("num_cores", 1)
    max_mem = resources.get("memory", "4G")
    extra_novo_args = " ".join(_novoalignCS_args_from_config(config, False))
    file_format = config["algorithm"].get("file_format", "BAMPE")

    if not file_exists(out_file):
        with curdir_tmpdir(base_dir=align_dir) as work_dir:
            with file_transaction(out_file) as tx_out_file:
                rg_info = get_rg_info(names)
                cmd = ("{novosort} -c {num_cores} -m {max_mem} --compression 0 "
                       " -n -t {work_dir} {in_bam} "
                       "| {novoalignCS} -o SAM '{rg_info}' -d {ref_file} -f /dev/stdin "
                       "  -F {file_format} -c {num_cores} {extra_novo_args} "
                       "| {samtools} view -b -S -u - "
                       "| {novosort} -c {num_cores} -m {max_mem} -t {work_dir} "
                       "  -o {tx_out_file} /dev/stdin")
                cmd = cmd.format(**locals())
                do.run(cmd, "novoalignCS: %s" % names["sample"], None,
                       [do.file_nonempty(tx_out_file), do.file_reasonable_size(tx_out_file, in_bam)])
    return out_file

# ## CSFASTQ to BAM alignment

def can_pipe(fastq_file):
    """novoalignCS support piping for all read lengths.
    """
    return True

def align_pipe(fastq_file, pair_file, ref_file, names, align_dir, data):
    """Perform piped alignment of csfastq input files, generating sorted output BAM.
    """
    pair_file = pair_file if pair_file else ""
    out_file = os.path.join(align_dir, "{0}-sort.bam".format(names["lane"]))
    if data.get("align_split"):
        final_file = out_file
        out_file, data = alignprep.setup_combine(final_file, data)
        fastq_file = alignprep.split_namedpipe_cl(fastq_file, data)
        if pair_file:
            pair_file = alignprep.split_namedpipe_cl(pair_file, data)
    else:
        final_file = None
    samtools = config_utils.get_program("samtools", data["config"])
    novoalignCS = config_utils.get_program("novoalignCS", data["config"])
    resources = config_utils.get_resources("novoalignCS", data["config"])
    num_cores = data["config"]["algorithm"].get("num_cores", 1)
    max_mem = resources.get("memory", "1G")
    extra_novo_args = " ".join(_novoalignCS_args_from_config(data["config"]))
    rg_info = get_rg_info(names)
    if not utils.file_exists(out_file) and (final_file is None or not utils.file_exists(final_file)):
        with utils.curdir_tmpdir() as work_dir:
            with file_transaction(out_file) as tx_out_file:
                tx_out_prefix = os.path.splitext(tx_out_file)[0]
                cmd = ("{novoalignCS} -o SAM '{rg_info}' -d {ref_file} -f {fastq_file} {pair_file} "
                       "  -c {num_cores} {extra_novo_args} "
                       "| {samtools} view -b -S -u - "
                       "| {samtools} sort -@ {num_cores} -m {max_mem} - {tx_out_prefix}")
                cmd = cmd.format(**locals())
                do.run(cmd, "novoalignCS: %s" % names["sample"], None,
                       [do.file_nonempty(tx_out_file), do.file_reasonable_size(tx_out_file, fastq_file)])
    data["work_bam"] = out_file
    return data

def _novoalignCS_args_from_config(config, need_quality=True):
    """Select novoalignCS options based on configuration parameters.
    """
    if need_quality:
        qual_format = config["algorithm"].get("quality_format", "").lower()
        qual_flags = ["-F", "XSQ" if qual_format == "SOLiD" else "BFAST"]
        #qual_flags = ["-F", "BFAST"]
    else:
        qual_flags = []
    multi_mappers = config["algorithm"].get("multiple_mappers")
    if multi_mappers is True:
        multi_flag = "Random"
    elif isinstance(multi_mappers, basestring):
        multi_flag = multi_mappers
    else:
        multi_flag = "None"
    multi_flags = ["-r"] + multi_flag.split()
    resources = config_utils.get_resources("novoalignCS", config)
    # default arguments for improved variant calling based on
    # comparisons to reference materials: turn off soft clipping and recalibrate
    if resources.get("options") is None:
        extra_args = ["-o", "FullNW", "-k"]
    else:
        extra_args = [str(x) for x in resources.get("options", [])]
    return qual_flags + multi_flags + extra_args

# Tweaks to add
# -k -t 200 -K quality calibration metrics
# paired end sizes

def align(fastq_file, pair_file, ref_file, out_base, align_dir, config,
          extra_args=None, names=None):
    """Align with novoalignCS.
    """
    rg_name = names.get("rg", None) if names else None
    out_file = os.path.join(align_dir, "{0}.sam".format(out_base))
    if not file_exists(out_file):
        cl = [config_utils.get_program("novoalignCS", config)]
        cl += _novoalignCS_args_from_config(config)
        cl += extra_args if extra_args is not None else []
        cl += ["-o", "SAM"]
        if rg_name:
            cl.append(r"'@RG\tID:{0}'".format(rg_name))
        cl += ["-d", ref_file, "-f", fastq_file]
        if pair_file:
            cl.append(pair_file)
        with file_transaction(out_file) as tx_out_file:
            cmd = "{cl} > {out_file}".format(cl=" ".join(cl), out_file=tx_out_file)
            do.run(cmd, "novoalignCS {rg_name}".format(**locals()), None)
    return out_file

# ## Indexing

@memoize_outfile(ext=".ndx")
def refindex(ref_file, kmer_size=None, step_size=None, out_file=None):
    cl = ["novoindex"]
    if kmer_size:
        cl += ["-k", str(kmer_size)]
    if step_size:
        cl += ["-s", str(step_size)]
    cl += [out_file, ref_file]
    subprocess.check_call(cl)

# ## Galaxy integration

# Optional galaxy location file. Falls back on remap_index_fn if not found
galaxy_location_file = "novoalignCS_indices.loc"

def remap_index_fn(ref_file):
    """Map sequence references to equivalent novoalignCS indexes.
    """
    checks = [os.path.splitext(ref_file)[0].replace("/seq/", "/novoalignCS/"),
              os.path.splitext(ref_file)[0] + ".ndx",
              ref_file + ".bs.ndx",
              ref_file + ".ndx"]
    for check in checks:
        if os.path.exists(check):
            return check
    return checks[0]
