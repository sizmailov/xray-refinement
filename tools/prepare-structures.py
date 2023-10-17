import logging
from multiprocessing import Pool
from pathlib import Path
from typing import List

import gemmi

# when running this preparation function (process_pdb) on remote runners,
# it is useful to add the current direcotory into PATH with the two lines:
# import sys
# sys.path.insert(1, str(Path.cwd()))

from arx.amber import create_topology_and_input
from arx.prepare import (
    add_missing_atoms,
    add_missing_b_factors,
    add_missing_occupancies,
    add_water,
    assign_protonation_states,
    copy_residue_names,
    expand_crystallographic_symmetries,
    expand_non_crystallographic_symmetries,
    expand_supercell,
    get_target_ph,
    neutralize_with_ions,
    read_pdb,
    remove_alternative_conformations,
    remove_ligands_and_water,
    remove_empty_chains,
    remove_hydrogens,
    renumber_residues,
    retain_only_standard_resnames,
    write_pdb,
)

logger = logging.getLogger("prepare-structure")


class StructurePipeInput:
    def __init__(self, st: gemmi.Structure, *, debug_dir: Path = None, seq=0):
        self.st: gemmi.Structure = st
        self.debug_dir = debug_dir
        self.seq = seq

    def do(self, func, *args, **kwargs):
        logger.info(f"{func.__name__}...")
        if self.debug_dir:
            write_pdb(self.st, self.fmt_debug_path(f"{func.__name__}.before"))
        result: gemmi.Structure = func(self.st, *args, **kwargs)
        if self.debug_dir:
            write_pdb(result, self.fmt_debug_path(f"{func.__name__}.after"))
        return StructurePipeInput(result, debug_dir=self.debug_dir, seq=self.seq + 1)

    def fmt_debug_path(self, name: str, ext="pdb") -> Path:
        self.debug_dir.mkdir(exist_ok=True, parents=True)
        return self.debug_dir / f"{self.seq:02d}_{name}.{ext}"


def create_parm7_rst7_from(
    structure: gemmi.Structure,
    solvent: gemmi.Structure,
    positive_ion: gemmi.Structure,
    negative_ion: gemmi.Structure,
    parm7_path: Path,
    rst7_path: Path,
    debug_dir: Path = None,
    supercell_size: int = 1,
) -> gemmi.Structure:
    target_ph = get_target_ph(structure)
    logger.info(f"pH is {target_ph}")

    reference = StructurePipeInput(structure, debug_dir=debug_dir / "reference")\
        .do(remove_ligands_and_water)\
        .do(remove_alternative_conformations)\
        .do(remove_empty_chains)\
        .do(retain_only_standard_resnames)\
        .do(expand_non_crystallographic_symmetries)\
        .do(expand_crystallographic_symmetries)\
        .do(expand_supercell, supercell_size=supercell_size)\
        .do(renumber_residues)
    # For further developement:
    # To handle nucleic acids one would .do(prepare_5_terminal_nucleic_acids) as the last step 
    reference_st = reference.st.clone()

    reference_protonation = StructurePipeInput(reference_st, debug_dir=debug_dir / "protonation_naming")\
        .do(add_missing_atoms)\
        .do(renumber_residues)\
        .do(remove_hydrogens, reference=reference_st)\
        .do(assign_protonation_states, target_ph=target_ph)
    reference_protonation_st = reference_protonation.st.clone()

    # This is a prototype of the code to handle gaps in chains
    # One would need to additionally import from arx.prepare:
    # find_gaps, apply_additional_ters, 
    # check_chain_and_residue_numbering
    #
    # reference_protonation_st, additional_ter_residues = find_gaps(reference_protonation.st)
    #
    # if len(additional_ter_residues) > 0:
    #     logger.info(f"Gaps have been detected at residues: {', '.join([str(t) for t in additional_ter_residues])}")
    #     reference_st = renumber_residues(apply_additional_ters(reference_st, additional_ter_residues))
    #     reference_protonation_st = renumber_residues(reference_protonation_st)
    #     assert check_chain_and_residue_numbering(reference_protonation_st, reference_st, strict=False)
    # else:
    #     assert check_chain_and_residue_numbering(reference_protonation_st, reference_protonation.st)

    # it _might_ be useful to run minimize_in_python before neutralizing with ions
    return (
        StructurePipeInput(reference_st, debug_dir=debug_dir)
        .do(copy_residue_names, reference=reference_protonation_st)
        .do(add_missing_atoms)
        .do(renumber_residues)  # needed match the reference after tleap, just in case
        .do(add_missing_b_factors, reference=reference_st)
        .do(add_missing_occupancies, reference=reference_st)
        .do(neutralize_with_ions, negative_ion=negative_ion, positive_ion=positive_ion)
        .do(add_water, water=solvent)
        .do(create_topology_and_input, parm7_path=parm7_path, rst7_path=rst7_path)
    ).st


def process_pdb(data_dir: Path, pdb_code: str, skip_if_exists: bool = True, supercell_size: int = 1):
    sm = data_dir / "small-molecules"
    if supercell_size > 1:
        out_dir = data_dir / "amber-topology-sc" / pdb_code
    else:
        out_dir = data_dir / "amber-topology" / pdb_code
    out_dir.mkdir(exist_ok=True, parents=True)
    wbox_path = (out_dir / "wbox.pdb").absolute()
    parm7_path = (out_dir / "wbox.parm7").absolute()
    rst7_path = (out_dir / "wbox.rst7").absolute()

    if (
        skip_if_exists
        and wbox_path.exists()
        and parm7_path.exists()
        and rst7_path.exists()
    ):
        logger.info(f"Skip {pdb_code}: exists")
        return
    logger.info(f"Start processing {pdb_code}...")
    wbox = create_parm7_rst7_from(
        structure=read_pdb(data_dir / "input" / pdb_code / f"{pdb_code}.pdb"),
        positive_ion=read_pdb(sm / "na.pdb"),
        negative_ion=read_pdb(sm / "cl.pdb"),
        solvent=read_pdb(sm / "spce.pdb"),
        parm7_path=parm7_path,
        rst7_path=rst7_path,
        debug_dir=out_dir / "debug",
        supercell_size=supercell_size
    )
    write_pdb(wbox, wbox_path)
    logger.info(f"Complete {pdb_code}!")


def get_all_available_pdb_codes(data_dir: Path) -> List[str]:
    return sorted(
        filename.stem[:4].lower()
        for filename in data_dir.glob("????")
        if filename.is_dir()
    )


def get_the_84_structures_codes(data_dir: Path) -> List[str]:
    from arx.dataset import the_84_structures_codes

    return list(the_84_structures_codes)


def get_pdb_codes(data_dir: Path) -> List[str]:
    return get_the_84_structures_codes(data_dir)


class PdbCodeProcessor:
    def __init__(self, data: Path):
        self.data_path = data

    def __call__(self, pdb_code: str):
        try:
            process_pdb(self.data_path, pdb_code, skip_if_exists=True)
        except Exception:
            logger.exception(f"Can't process {pdb_code}")


def main():
    logging.basicConfig()
    logger.setLevel(logging.INFO)

    data = Path.cwd() / "data"
    pdb_codes = get_pdb_codes(data / "input")

    n_parallel = 4

    process_pdb_code = PdbCodeProcessor(data)

    with Pool(n_parallel) as p:
        p.map(process_pdb_code, pdb_codes)


if __name__ == "__main__":
    main()
