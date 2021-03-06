# -*- coding: utf-8 -*-

import os
from functools import partial
from pathlib import Path
from contextlib import contextmanager
import shutil
from pymatgen import Structure
import concurrent.futures
from pymatgen.analysis.graphs import StructureGraph
from pymatgen.analysis.local_env import JmolNN, CrystalNN
from pymatgen.io.ase import AseAtomsAdaptor
from sklearn.preprocessing import StandardScaler
from scipy.spatial import KDTree
import tempfile
import logging
from ase.visualize.plot import plot_atoms
from ase.io import read, write
from ase.build import niggli_reduce
import matplotlib.pyplot as plt
from tqdm.autonotebook import tqdm
import numpy as np
import pandas as pd
from .rmsd import parse_periodic_case, kabsch_rmsd
from .utils import get_structure_list, get_hash, attempt_supercell_pymatgen
from collections import defaultdict

logger = logging.getLogger("RemoveDuplicates")
logger.setLevel(logging.INFO)

# ToDo: add XTalComp support
# ToDo: more useful error message when file cannot be read


class RemoveDuplicates:
    """
    A RemoveDuplicates object operates on a collection of structure and allows
        - Removal of duplicates on the collection of structures using different methods, using the main
        function run_filtering()
        - Basic comparisons between different RemoveDuplicates objects (e.g. comparing which one contains more duplicates)
    """

    def __init__(
        self,
        structure_list: list,
        cached: bool = False,
        method="standard",
        try_supercell=True,
    ):

        self.structure_list = structure_list
        self.reduced_structure_dict = None
        self.cached = cached
        self.pairs = None
        self.method = method
        self.similar_composition_tuples = []
        self.try_supercell = try_supercell
        self.tempdirpath = None
        self.atom_threshold = 5000
        self.graph_dict = {}

    def __repr__(self):
        return f"RemoveDuplicates on {len(self.structure_list)!r} structures"

    @classmethod
    def from_folder(
        cls,
        folder,
        cached: bool = False,
        extension="cif",
        method="standard",
        try_supercell=True,
    ):
        """

        Args:
            folder (str): path to folder that is used for construction of the RemoveDuplicates object
            reduced_structure_dir (str): name in which tempera
            extension:
            remove_reduced_structure_dir:
            method:

        Returns:

        """
        sl = get_structure_list(folder, extension)
        return cls(sl, cached, method, try_supercell)

    # Implement some logic in case someone wants to compare dbs
    def __len__(self):
        if self.pairs is not None:
            return len(self.pairs)
        else:
            return 0

    def __eq__(self, other):
        return set(self.pairs) == set(other.pairs)

    def __gt__(self, other):
        return len(self.pairs) > len(other.pairs)

    def __lt__(self, other):
        return len(self.pairs) < len(other.pairs)

    def __ge__(self, other):
        return len(self.pairs) >= len(other.pairs)

    def __le__(self, other):
        return len(self.pairs) <= len(other.pairs)

    def __iter__(self):
        return iter(self.pairs)

    def get_reduced_structure(self, structure):
        sname = Path(structure).name
        stem = Path(structure).stem
        try:
            if self.cached:
                self.reduced_structure_dict = {}
            try:
                # Cif reader in ASE seems more stable to me, especially for CSD data
                atoms = read(structure)
                if len(atoms) > self.atom_threshold:
                    logger.error("Larger than threshold %s", stem)
            except Exception:
                logger.error("Could not read structure %s", stem)
            else:
                niggli_reduce(atoms)
                if not self.cached:
                    write(os.path.join(self.tempdirpath, sname), atoms)
                else:
                    crystal = AseAtomsAdaptor.get_structure(atoms)
                    self.reduced_structure_dict[stem] = crystal
        except Exception:
            logger.error("Could not read structure %s", stem)
        return stem

    def get_reduced_structures(self):
        """
        To make calculations cheaper, we first get Niggli cells.
        If caching is turned off, the structures are written to a temporary directory (useful for large
        databases), otherwise the reduced structures are stored in memory.
        """
        if not self.cached:
            self.tempdirpath = tempfile.mkdtemp()
            self.reduced_structure_dir = self.tempdirpath
        logger.info("creating reduced structures")
        with concurrent.futures.ProcessPoolExecutor() as executor:
            for _ in tqdm(
                executor.map(self.get_reduced_structure, self.structure_list),
                total=len(self.structure_list),
            ):
                logger.debug("reduced structure for {} created".format(_))

    @staticmethod
    def get_scalar_features(structure: Structure):
        """
        Computes number of atoms and density for a pymatgen structure object.
        Args:
            structure:

        Returns:

        """
        volume = structure.volume
        density = structure.density
        return volume, density

    @staticmethod
    def get_scalar_features_from_file(structure_file):
        """
        Computes number of atoms and density for structure file.
        Args:
            structure_file:

        Returns:

        """
        structure = Structure.from_file(structure_file)
        volume = structure.volume
        density = structure.density
        return volume, density

    @staticmethod
    def get_scalar_df(reduced_structure_list: list):
        """

        Args:
            reduced_structure_list:

        Returns:

        """
        feature_list = []
        logger.info("creating scalar features")

        with concurrent.futures.ProcessPoolExecutor() as executor:
            for structure, result in tqdm(
                zip(
                    reduced_structure_list,
                    executor.map(
                        RemoveDuplicates.get_scalar_features_from_file,
                        reduced_structure_list,
                    ),
                ),
                total=len(reduced_structure_list),
            ):
                features = {
                    "name": structure,
                    "volume": result[0],
                    "density": result[1],
                }
                feature_list.append(features)
            df = pd.DataFrame(feature_list)

            df["density"] = df["density"].astype(np.float16)
            df["volume"] = df["volume"].astype(np.int16)

            scaler = StandardScaler()
            scaled_values = scaler.fit_transform(df[["volume", "density"]])
            df[["volume", "density"]] = scaled_values

            logger.debug("the dataframe looks like %s", df.head())
        return df

    @staticmethod
    def get_scalar_df_cached(reduced_structure_dict: dict):
        """

        Args:
            reduced_structure_dict:

        Returns:

        """
        feature_list = []
        logger.info("creating scalar features")
        for structure in tqdm(reduced_structure_dict):
            crystal = reduced_structure_dict[structure]
            volume, density = RemoveDuplicates.get_scalar_features(crystal)
            features = {
                "name": structure,
                "volume": volume,
                "density": density,
            }
            feature_list.append(features)
        df = pd.DataFrame(feature_list)
        df["density"] = df["density"].astype(np.float16)
        df["volume"] = df["volume"].astype(np.int16)
        scaler = StandardScaler()
        scaled_values = scaler.fit_transform(df[["volume", "density"]])
        df[["volume", "density"]] = scaled_values
        logger.debug("the dataframe looks like %s", df.head())

        return df

    @staticmethod
    def get_scalar_distance_matrix(
        scalar_feature_df: pd.DataFrame, threshold: float = 0.01
    ) -> list:
        """
        Get structures that probably have the same composition.

        Args:
            scalar_feature_df: pandas Dataframe object with the scalar features
            threshold: threshold: threshold for the Euclidean distance between structure features

        Returns:
            list of tuples which Euclidean distance is under threshold

        """
        x = scalar_feature_df.drop(columns=["name"]).values

        tree = KDTree(x)

        duplicates = []
        for i, row in enumerate(x):
            g = tree.query_ball_point(row, threshold)
            if len(g) >= 2:
                for _, index in enumerate(g):
                    if index != i:
                        duplicates.append(tuple((i, index)))

        del tree
        del x

        duplicates = list(set(map(tuple, map(sorted, duplicates))))

        logger.debug("found {} composition duplicates".format(duplicates))

        return duplicates

    @staticmethod
    def compare_rmsd(
        tupellist: list,
        scalar_feature_df: pd.DataFrame,
        threshold: float = 0.1,
        try_supercell: bool = True,
        reduced_structure_dict=None,
    ) -> list:
        """

        Args:
            tupellist (list): list of indices of structures with identical compostion
            scalar_feature_df (pandas dataframe):
            threshold:
            try_supercell (bool): switch which control whether expansion to supercell is tested

        Returns:

        """
        logger.info("doing RMSD comparison")
        pairs = []
        for items in tqdm(tupellist):
            if reduced_structure_dict is not None:
                if items[0] != items[1]:
                    crystal_a = reduced_structure_dict[
                        scalar_feature_df.iloc[items[0]]["name"]
                    ]
                    crystal_b = reduced_structure_dict[
                        scalar_feature_df.iloc[items[1]]["name"]
                    ]

                    _, P, _, Q = parse_periodic_case(
                        crystal_a,
                        crystal_b,
                        try_supercell,
                        pymatgen=True,
                        get_reduced_structure=False,
                    )

                    logger.debug("Lengths are %s, %s", len(P), len(Q))
                    rmsd_result = kabsch_rmsd(P, Q, translate=True)
                    logger.debug("The Kabsch RMSD is %s", rmsd_result)
                    if rmsd_result < threshold:
                        pairs.append(items)
            else:
                if items[0] != items[1]:
                    _, P, _, Q = parse_periodic_case(
                        scalar_feature_df.iloc[items[0]]["name"],
                        scalar_feature_df.iloc[items[1]]["name"],
                        try_supercell,
                        get_reduced_structure=False,
                    )
                    logger.debug(
                        "Comparing %s and %s",
                        scalar_feature_df.iloc[items[0]]["name"],
                        scalar_feature_df.iloc[items[1]]["name"],
                    )
                    logger.debug("Lengths are %s, %s", len(P), len(Q))
                    rmsd_result = kabsch_rmsd(P, Q, translate=True)
                    logger.debug("The Kabsch RMSD is %s", rmsd_result)
                    if rmsd_result < threshold:
                        pairs.append(items)
        return pairs

    def compare_graph_pair_cached(self, items):
        sgraph_a = self.graph_dict[items[0]]
        sgraph_b = self.graph_dict[items[1]]
        try:
            if sgraph_a == sgraph_b:
                logger.debug("Found duplicate")
                return items
        except ValueError:
            logger.debug("Structures were probably not duplicates")
            return False

    def precompute_graphs(
        self, tupellist, cached=False, cached_graphs=True, method="jmolnn"
    ):
        unique_indices = set(sum(tupellist, ()))

        graph_computer = partial(self.compute_graph, cached=cached, method=method,)

        indices = []
        results = []
        with concurrent.futures.ProcessPoolExecutor() as executor:
            for index, res in zip(
                unique_indices, executor.map(graph_computer, unique_indices)
            ):
                indices.append(index)
                results.append(res)

        self.graph_dict[index] = dict(zip(indices, results))

    def compute_graph(self, index, cached=False, method="jmolnn"):
        if cached:
            s = self.reduced_structure_dict[
                self.scalar_feature_matrix.iloc[index]["name"]
            ]
        else:
            s = Structure.from_file(self.scalar_feature_matrix.iloc[index]["name"])

        if method == "jmolnn":
            nn_strategy = JmolNN()
        elif method == "crystalgraph":
            nn_strategy = CrystalNN()
        else:
            nn_strategy = JmolNN()

        graph = StructureGraph.with_local_env_strategy(s, nn_strategy)

        return graph

    def compare_graphs(self, tupellist: list) -> list:
        """

        Args:
            tupellist:

        Returns:

        """
        logger.info("constructing and comparing structure graphs")
        pairs = []

        self.precompute_graphs(tupellist)

        with concurrent.futures.ProcessPoolExecutor() as executor:
            for _, result in tqdm(
                zip(
                    tupellist, executor.map(self.compare_graph_pair_cached, tupellist),
                ),
                total=len(tupellist),
            ):
                logger.debug(result)
                if result:
                    pairs.append(result)

        return pairs

    def get_graph_hash_dict(self, structure):
        crystal = Structure.from_file(structure)
        name = Path(structure).name
        graph_hash = get_hash(crystal)
        self.hash_dict[graph_hash].append(name)

    def get_graph_hash_dicts(self):
        self.hash_dict = defaultdict(list)
        with concurrent.futures.ProcessPoolExecutor() as executor:
            for structure in tqdm(
                zip(
                    self.structure_list,
                    executor.map(self.get_graph_hash_dict, self.structure_list),
                ),
                total=len(self.structure_list),
            ):
                logger.debug("getting hash for %s", structure)

        return self.hash_dict

    @contextmanager
    def run_filtering(self):
        """

        Returns:

        """
        try:
            logger.info("running filtering workflow")

            self.get_reduced_structures()

            if not self.cached:
                self.reduced_structure_list = get_structure_list(
                    self.reduced_structure_dir
                )
                logger.debug(
                    "we have %s reduced structures", len(self.reduced_structure_list)
                )

                self.scalar_feature_matrix = RemoveDuplicates.get_scalar_df(
                    self.reduced_structure_list
                )
            else:
                logger.debug(
                    "we have %s reduced structures", len(self.reduced_structure_dict)
                )
                self.scalar_feature_matrix = RemoveDuplicates.get_scalar_df_cached(
                    self.reduced_structure_dict
                )

            logger.debug(
                "columns of dataframe are %s", self.scalar_feature_matrix.columns
            )

            self.similar_composition_tuples = RemoveDuplicates.get_scalar_distance_matrix(
                self.scalar_feature_matrix
            )

            if self.method == "standard":

                self.pairs = self.compare_graphs(self.similar_composition_tuples)

            elif self.method == "rmsd":
                self.pairs = RemoveDuplicates.compare_rmsd(
                    tupellist=self.similar_composition_tuples,
                    scalar_feature_df=self.scalar_feature_matrix,
                    try_supercell=self.try_supercell,
                    reduced_structure_dict=self.reduced_structure_dict,
                )

            elif self.method == "rmsd_graph":
                self.rmsd_pairs = RemoveDuplicates.compare_rmsd(
                    tupellist=self.similar_composition_tuples,
                    scalar_feature_df=self.scalar_feature_matrix,
                    try_supercell=self.try_supercell,
                    reduced_structure_dict=self.reduced_structure_dict,
                )

                self.pairs = self.compare_graphs(self.rmsd_pairs)

            elif self.method == "hash":
                raise NotImplementedError
                # RemoveDuplicates.get_graph_hash_dict(self.structure_list)
        finally:
            if self.tempdirpath and os.path.exists(self.tempdirpath):
                logger.debug("now i am cleaning up")
                shutil.rmtree(self.tempdirpath)

    @staticmethod
    def get_rmsd_matrix():
        return NotImplementedError

    @staticmethod
    def get_jacard_graph_distance_matrix():
        return NotImplementedError

    @property
    def number_of_duplicates(self):
        try:
            if self.pairs:
                number_duplicates = len(self.pairs)
            else:
                number_duplicates = 0
        except AttributeError:
            number_duplicates = None
        return number_duplicates

    @property
    def duplicates(self):
        try:
            if self.pairs:
                duplicates = []
                for items in self.pairs:
                    name1 = Path(self.scalar_feature_matrix.iloc[items[0]]["name"]).name
                    name2 = Path(self.scalar_feature_matrix.iloc[items[1]]["name"]).name
                    duplicates.append((name1, name2))
            else:
                duplicates = 0
        except AttributeError:
            duplicates = None
        return duplicates

    def inspect_duplicates(self, mode: str = "ase"):
        if mode == "ase":
            if self.pairs:
                for items in self.pairs:
                    fig, axarr = plt.subplots(1, 2, figsize=(15, 5))
                    plot_atoms(
                        read(self.scalar_feature_matrix.iloc[items[0]]["name"]),
                        axarr[0],
                    )
                    plot_atoms(
                        read(self.scalar_feature_matrix.iloc[items[1]]["name"]),
                        axarr[1],
                    )
            else:
                logger.info("no duplicates to plot")

    def remove_duplicates(self):
        try:
            for items in self.pairs:
                os.remove(self.scalar_feature_matrix.iloc[items[0]]["name"])

            # Should we now also clean the pair list?
        except Exception:
            logger.error("Could not delete duplicates")
