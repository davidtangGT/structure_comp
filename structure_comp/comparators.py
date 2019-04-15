#!/usr/bin/python
# -*- coding: utf-8 -*-

# Get basic statistics describing the database
# Compare a structure to a database

from tqdm.autonotebook import tqdm
import logging
from pymatgen import Structure
from pymatgen.analysis.graphs import StructureGraph
from pymatgen.analysis.local_env import JmolNN
from .utils import get_structure_list, get_rmsd, closest_index, tanimoto_distance, get_number_bins
import random
from scipy.spatial import distance
from sklearn.linear_model import HuberRegressor
from sklearn.metrics import mean_squared_error, r2_score
from sklearn.metrics.pairwise import euclidean_distances
from scipy.stats import pearsonr, ks_2samp, mannwhitneyu, ttest_ind, anderson_ksamp
import numpy as np
import pandas as pd
from sklearn.cluster import KMeans
import matplotlib.pyplot as plt
from scipy import ndimage
import concurrent.futures
from functools import partial

logger = logging.getLogger('RemoveDuplicates')
logger.setLevel(logging.DEBUG)

# ToDo (maybe) make sure that input data is numeric?
# Todo: grid search for kernel width in MMD test


class Statistics():
    def __init__(self):
        pass

    @staticmethod
    def _get_one_graph_comparison(structure_list_a, structure_list_b, _):
        random_selection_1 = random.sample(structure_list_a, 1)[0]
        random_selection_2 = random.sample(structure_list_b, 1)[0]
        crystal_a = Structure.from_file(random_selection_1)
        crystal_b = Structure.from_file(random_selection_2)
        nn_strategy = JmolNN()
        sgraph_a = StructureGraph.with_local_env_strategy(
            crystal_a, nn_strategy)
        sgraph_b = StructureGraph.with_local_env_strategy(
            crystal_b, nn_strategy)
        return sgraph_a.diff(sgraph_b, strict=False)['dist']

    @staticmethod
    def _randomized_graphs(structure_list_a: list,
                           structure_list_b: list,
                           iterations: int = 5000) -> list:
        """
        Randomly sample structures from the structure list and compare their Jaccard graph distance.

        Args:
            structure_list_a (list): list of paths to structures
            structure_list_b (list): list of paths to structures
            iterations (int): Number of comparisons (sampling works with replacement, i.e. the same pair might
            be sampled several times).

        Returns:
            list of length iterations of the Jaccard distances
        """

        diffs = []

        get_one_graph_comparison_partial = partial(
            Statistics._get_one_graph_comparison, structure_list_a,
            structure_list_b)

        with concurrent.futures.ProcessPoolExecutor() as executor:
            logger.debug('iterating for graph comparisons')

            for diff in tqdm(
                    executor.map(get_one_graph_comparison_partial,
                                 range(iterations)),
                    total=len(range(iterations))):
                diffs.append(diff)

        return diffs

    @staticmethod
    def _get_one_randomized_structure_property(structure_list_a,
                                               structure_list_b, feature, _):
        random_selection_1 = random.sample(structure_list_a, 1)[0]
        random_selection_2 = random.sample(structure_list_b, 1)[0]
        crystal_a = Structure.from_file(random_selection_1)
        crystal_b = Structure.from_file(random_selection_2)
        if feature == 'density':
            diff = np.abs(crystal_a.density - crystal_b.density)
        elif feature == 'num_sites':
            diff = np.abs(crystal_a.num_sites - crystal_b.num_sites)
        elif feature == 'volume':
            diff = np.abs(crystal_a.volume - crystal_b.volume)
        return diff

    @staticmethod
    def _randomized_structure_property(structure_list_a: list,
                                       structure_list_b: list,
                                       feature: str = 'density',
                                       iterations: int = 5000) -> list:
        """

        Args:
            structure_list_a (list): list of paths to structures
            structure_list_b (list): list of paths to structures
            feature (str): property that is used for the structure comparisons, available options are
                density, num_sites, volume. Default is density.
            iterations (int): number of comparisons (sampling works with replacement, i.e. the same pair might
            be sampled several times).

        Returns:

        """
        diffs = []
        get_one_randomized_structure_property_partial = partial(
            Statistics._get_one_randomized_structure_property,
            structure_list_a, structure_list_b, feature)

        with concurrent.futures.ProcessPoolExecutor() as executor:
            logger.debug('iterating for graph comparisons')

            for diff in tqdm(
                    executor.map(get_one_randomized_structure_property_partial,
                                 range(iterations)),
                    total=len(range(iterations))):
                diffs.append(diff)

        return diffs

    @staticmethod
    def _get_one_rmsd(structure_list_a, structure_list_b, _):
        random_selection_1 = random.sample(structure_list_a, 1)[0]
        random_selection_2 = random.sample(structure_list_b, 1)[0]
        a = get_rmsd(random_selection_1, random_selection_2)
        return a

    @staticmethod
    def _randomized_rmsd(structure_list_a: list,
                         structure_list_b: list,
                         iterations: float = 5000) -> list:
        """

        Args:
            structure_list_a (list): list of paths to structures
            structure_list_b (list): list of paths to structures
            iterations (int): number of comparisons (sampling works with replacement, i.e. the same pair might
                  be sampled several times).

        Returns:

        """
        rmsds = []

        with concurrent.futures.ProcessPoolExecutor() as executor:
            logger.debug('iterating for graph comparisons')

            get_one_rmsd_partial = partial(Statistics._get_one_rmsd,
                                           structure_list_a, structure_list_b)
            for rmsd in tqdm(
                    executor.map(get_one_rmsd_partial, range(iterations)),
                    total=len(range(iterations))):
                rmsds.append(rmsd)

        return rmsds

    @staticmethod
    def trimean(data):
        q1 = np.quantile(data, 0.25)
        q3 = np.quantile(data, 0.75)
        return (q1 + 2 * np.median(data) + q3) / 4

    @staticmethod
    def interquartile_mean(data):
        q1 = np.quantile(data, 0.25)
        q3 = np.quantile(data, 0.75)
        sorted_data = np.sort(data)
        trimmed_data = sorted_data[(sorted_data >= q1) & (sorted_data <= q3)]
        return np.mean(trimmed_data)

    @staticmethod
    def midhinge(data):
        q1 = np.quantile(data, 0.25)
        q3 = np.quantile(data, 0.75)
        return np.mean([q1, q3])

    @staticmethod
    def val_range(data):
        max_val = np.max(data)
        min_val = np.min(data)
        return abs(max_val - min_val)

    @staticmethod
    def mid_range(data):
        return (np.max(data) + np.min(data)) / 2


class DistStatistic(Statistics):
    def __init__(self, structure_list=None, property_list=None):
        self.structure_list = structure_list
        self.property_list = property_list
        self.feature_names = None

        if isinstance(property_list, pd.DataFrame):
            logger.debug('Input seems to be a dataframe')
            self.list_of_list_mode = True
            self.feature_names = self.property_list.columns.values
            logger.debug('will use %s as feature names', self.feature_names)
            _tmp_property_list = []

            for feature in self.feature_names:
                _tmp_property_list.append(
                    self.property_list[feature].values.tolist())
            self.property_list = _tmp_property_list

        else:
            if all(isinstance(i, list) for i in property_list):
                self.list_of_list_mode = True
                self.feature_names = [
                    '_'.join(['feature', i])
                    for i in range(len(self.property_list))
                ]
            else:
                self.list_of_list_mode = False

    def __repr__(self):
        return 'DistStatistic'

    @classmethod
    def from_folder(cls, folder: str, extension: str = 'cif'):
        """

        Args:
            folder (str): name of the folder which is used to create the structure list
            extension (str): extension of the structure files

        Returns:

        """
        sl = get_structure_list(folder, extension)
        return cls(sl)

    def randomized_graphs(self, iterations: int = 5000) -> list:
        """
        Returns iterations times the Jaccard distance between structure graph of two randomly chosen structures

        Args:
            iterations (int): number of comparisons (sampling works with replacement, i.e. the same pair might
                  be sampled several times).

        Returns:
            list of jaccard distances
        """
        jaccards = self._randomized_graphs(self.structure_list,
                                           self.structure_list, iterations)
        return jaccards

    def randomized_structure_property(self,
                                      feature: str = 'density',
                                      iterations: int = 5000) -> list:
        """
        Returns iterations times the Euclidean distance between two randomly chosen structures
        Args:
            feature (str): property that is used for the structure comparisons, available options are
                density, num_sites, volume. Default is density.
            iterations (int): number of comparisons (sampling works with replacement, i.e. the same pair might
                  be sampled several times).

        Returns:
            list of property distances
        """
        distances = self._randomized_structure_property(
            self.structure_list, self.structure_list, feature, iterations)
        return distances

    def randomized_rmsd(self, iterations: int = 5000) -> list:
        """
        Returns iterations times the Kabsch RMSD between two randomly chosen structures
        Args:
            iterations (int): number of comparisons (sampling works with replacement, i.e. the same pair might
                  be sampled several times).

        Returns:
            list of Kabsch RMSDs
        """
        distances = self._randomized_rmsd(self.structure_list,
                                          self.structure_list, iterations)
        return distances


class DistComparison():
    """
    Comparator to compare the difference or similarity between two distributions. 
    The idea is here to save the test statistics to the object such that we can
    then implement some dunder methods to compare different Comparator objects and
    e.g. find out which distributions are most similar to each other. 
    """

    def __init__(self,
                 structure_list_1: list = None,
                 structure_list_2: list = None,
                 property_list_1: [list, pd.DataFrame] = None,
                 property_list_2: [list, pd.DataFrame] = None):
        """

        Args:
            structure_list_1 (list):
            structure_list_2 (list):
            property_list_1 (list or pd.DataFrame):
            property_list_2 (list or pd.DataFrame): 
            
        """
        self.structure_list_1 = structure_list_1
        self.structure_list_2 = structure_list_2
        self.property_list_1 = property_list_1
        self.property_list_2 = property_list_2
        self.feature_names = []
        self.qq_statistics = {}
        self.properties_statistics = {}
        self.rmsds = None
        self.jaccards = None
        self.random_structure_property = {}

        if not isinstance(self.property_list_1, type(self.property_list_2)):
            raise ValueError('The two property inputs must be of same type')

        # Check if input is a dataframe. If this is the case, extract the column names
        # and convert it to list of lists
        if isinstance(property_list_1, pd.DataFrame):
            logger.debug('Input seems to be a dataframe')
            self.list_of_list_mode = True
            self.feature_names = self.property_list_1.columns.values
            logger.debug('will use %s as feature names', self.feature_names)
            _tmp_property_list_1 = []

            for feature in self.feature_names:
                _tmp_property_list_1.append(
                    self.property_list_1[feature].values.tolist())
            self.property_list_1 = _tmp_property_list_1

            _tmp_property_list_2 = []
            for feature in self.feature_names:
                _tmp_property_list_2.append(
                    self.property_list_2[feature].values.tolist())
            self.property_list_2 = _tmp_property_list_2

            assert len(self.property_list_1) == len(self.feature_names)
            assert len(self.property_list_2) == len(self.feature_names)

        else:
            # Check if the input is a list of list (i.e. multiple feature columns)
            # if this is the case, we have to iterate over the lists to compute the test statistics
            if all(isinstance(i, list) for i in property_list_1):
                if all(isinstance(i, list) for i in property_list_2):
                    self.list_of_list_mode = True
                    self.feature_names = [
                        '_'.join(['feature', i])
                        for i in range(len(self.property_list_1))
                    ]
                else:
                    logger.error(
                        'One input seems to be a list of list whereas the other one is not. '
                        'The property lists must be both of the same type. Please check your inputs.'
                    )
            else:
                if all(isinstance(i, list) for i in property_list_2):
                    logger.error(
                        'One input seems to be a list of list whereas the other one is not. '
                        'The property lists must be both of the same type. Please check your inputs.'
                    )
                else:
                    self.list_of_list_mode = False

    def __repr__(self):
        return 'DistComparison'

    def __len__(self):
        return len(self.structure_list_1) + len(self.structure_list_2) + len(
            self.property_list_1) + len(self.property_list_2)

    @classmethod
    def from_folders(cls,
                     folder_1: str,
                     folder_2: str,
                     property_list_1: [list, pd.DataFrame] = [],
                     property_list_2: [list, pd.DataFrame] = [],
                     extension='cif'):
        """Constructor method for a DistComparison object"""
        sl_1 = get_structure_list(folder_1, extension)
        sl_2 = get_structure_list(folder_2, extension)
        return cls(sl_1, sl_2, property_list_1, property_list_2)

    def randomized_graphs(self, iterations: int = 5000) -> list:
        """
        Returns iterations times the Jaccard distance between structure graph of two randomly chosen structures

        Args:
            iterations (int): number of comparisons (sampling works with replacement, i.e. the same pair might
                  be sampled several times).

        Returns:
            list of jaccard distances
        """
        jaccards = self._randomized_graphs(self.structure_list_1,
                                           self.structure_list_2, iterations)
        self.jaccards = jaccards
        return jaccards

    def randomized_structure_property(self,
                                      feature: str = 'density',
                                      iterations: int = 5000) -> list:
        """
        Returns iterations times the Euclidean distance between two randomly chosen structures

        Args:
            feature (str): property that is used for the structure comparisons, available options are
                density, num_sites, volume. Default is density.
            iterations (int): number of comparisons (sampling works with replacement, i.e. the same pair might
                  be sampled several times).

        Returns:
            list of property distances

        """
        distances = self._randomized_structure_property(
            self.structure_list_1, self.structure_list_2, feature, iterations)
        self.random_structure_property[feature] = distances
        return distances

    def randomized_rmsd(self, iterations: int = 5000) -> list:
        """
        Returns iterations times the Kabsch RMSD between two randomly chosen structures

        Args:
            iterations (int): number of comparisons (sampling works with replacement, i.e. the same pair might
                  be sampled several times).


        Returns:
            list of Kabsch RMSDs

        """
        distances = self._randomized_rmsd(self.structure_list_1,
                                          self.structure_list_2, iterations)
        self.rmsds = distances
        return distances

    @staticmethod
    def _single_qq_test(pl_1, pl_2, plot: bool = False):

        if len(pl_1) > len(pl_2):
            property_list_1 = pl_1
            property_list_2 = pl_2
        else:
            property_list_1 = pl_2
            property_list_2 = pl_1

        # Calculate quantiles
        property_list_1.sort()
        quantile_levels1 = np.arange(
            len(property_list_1), dtype=float) / len(property_list_1)

        property_list_2.sort()
        quantile_levels2 = np.arange(
            len(property_list_2), dtype=float) / len(property_list_2)

        # Use the smaller set of quantile levels to create the plot
        quantile_levels = quantile_levels2

        # We already have the set of quantiles for the smaller data set
        quantiles2 = property_list_2

        # We find the set of quantiles for the larger data set using linear interpolation
        quantiles1 = np.interp(quantile_levels, quantile_levels1,
                               property_list_1)

        maxval = max(property_list_1[-1], property_list_2[-1])
        minval = min(property_list_1[0], property_list_2[0])

        hr = HuberRegressor()
        hr.fit(quantiles1.reshape(-1, 1), quantiles2)
        predictions = hr.predict(quantiles1.reshape(-1, 1))
        mse = mean_squared_error(predictions, quantiles2)
        r2 = r2_score(predictions, quantiles2)
        pearson = pearsonr(quantiles1, quantiles2)

        if plot:
            plt.scatter(quantiles1, quantiles2)
            plt.plot([minval - minval * 0.1, maxval + maxval * 0.1],
                     [minval - minval * 0.1, maxval + maxval * 0.1], '--k')
            plt.plot(quantiles1, predictions, label='Huber Regression')
            plt.legend()

        results_dict = {
            'mse': mse,
            'r2': r2,
            'pearson_correlation_coefficient': pearson[0],
            'pearson_p_value': pearson[1],
        }

        return results_dict

    def qq_test(self, plot: bool = True) -> dict:
        """
        Performs a qq analysis and optionally a plot to compare two distributions.
        Works also for samples of unequal length by using interpolation to find the quantiles of the larger
        sample.
        
        As a measure of 'linearity' we perform a Huber linear regression and return the MSE and R^2 scores of the fit
        and pearson correlation coefficient with two-sided p-value

        Compare https://stackoverflow.com/questions/42658252/how-to-create-a-qq-plot-between-two-samples-of-different-size-in-python,
        from which I took the main outline of this function
        and https://www.itl.nist.gov/div898/handbook/eda/section3/eda33o.htm which contains background information. 

        Args:
            plot (bool): if true, it returns a qq plot with diagonal guideline as well as the Huber regression,
                use %matplotlib inline or %matplotlib notebook when using a jupyter notebook to show the plot inline 

        Returns:
            dictionary of dictionaries with the following statistics
            mse
            r2
            pearson_correlation_coefficient
            pearson_p_value

        """
        if self.list_of_list_mode:
            # concurrently loop of the different feature columns.
            with concurrent.futures.ProcessPoolExecutor() as executor:
                logger.debug('looping over feature columns for QQ statistics')
                partial_single_qq = partial(
                    DistComparison._single_qq_test, plot=plot)
                out_dict = {}
                for i, results_dict in enumerate(
                        executor.map(partial_single_qq, self.property_list_1,
                                     self.property_list_2)):
                    logger.debug('Creating QQ statistics for %s',
                                 self.feature_names[i])
                    self.qq_statistics[self.feature_names[i]] = results_dict
                    out_dict[self.feature_names[i]] = results_dict
            return out_dict
        else:
            out_dict = {}
            results_dict = DistComparison._single_qq_test(
                self.property_list_1, self.property_list_2, plot)
            self.qq_statistics[self.feature_names] = results_dict
            out_dict[self.feature_names] = results_dict
            return out_dict

    @staticmethod
    def _mutual_information_2d(x, y, sigma_ratio=0.1, normalized=False):
        """
        Taken from https://gist.github.com/GaelVaroquaux/ead9898bd3c973c40429
        Modified to automatically adjust bin width, if the distributions to not have the same width,
        both are binned to the optimal number of bins for the shorter distribution.

        Args:
            y:
            normalized:

        Returns:

        """
        EPS = np.finfo(float).eps
        width_x = max(x) - min(x)
        width_y = max(y) - min(y)
        stdev_x = np.std(x)
        stdev_y = np.std(y)

        if width_x < width_y:
            bin = get_number_bins(x)
        else:
            bin = get_number_bins(y)

        if stdev_x < stdev_y:
            sigma = sigma_ratio * stdev_x
        else:
            sigma = sigma_ratio * stdev_y

        bins = (bin, bin)

        # make joint histogram
        jh = np.histogram2d(x, y, bins=bins)[0]

        # smooth the jh with a gaussian filter of given sigma
        ndimage.gaussian_filter(jh, sigma=sigma, mode='constant', output=jh)

        # compute marginal histograms
        jh = jh + EPS
        sh = np.sum(jh)
        jh = jh / sh
        s1 = np.sum(jh, axis=0).reshape((-1, jh.shape[0]))
        s2 = np.sum(jh, axis=1).reshape((jh.shape[1], -1))

        # Normalised Mutual Information of:
        # Studholme,  jhill & jhawkes (1998).
        # "A normalized entropy measure of 3-D medical image alignment".
        # in Proc. Medical Imaging 1998, vol. 3338, San Diego, CA, pp. 132-143.
        if normalized:
            mi = ((np.sum(s1 * np.log(s1)) + np.sum(s2 * np.log(s2))) / np.sum(
                jh * np.log(jh))) - 1
        else:
            mi = (np.sum(jh * np.log(jh)) - np.sum(s1 * np.log(s1)) - np.sum(
                s2 * np.log(s2)))

        return mi

    # MMD test taken from https://github.com/paruby/ml-basics/blob/master/Statistical%20Hypothesis%20Testing.ipynb,
    # maybe use shogon
    @staticmethod
    def _optimal_kernel_width(samples):
        """
        Following a example from Dougal J. Sutherland use the median pairwise squared distances as heuristic.
        Args:
            samples:

        Returns:

        """
        sub = np.vstack(samples)
        sub = sub[np.random.choice(
            sub.shape[0], min(1000, sub.shape[0]), replace=False)]
        d2 = euclidean_distances(sub, squared=True)
        med = np.median(
            d2[np.triu_indices_from(d2, k=1)], overwrite_input=True)
        return med

    @staticmethod
    def _gaussian_kernel(z, length):
        z = z[:, :, None]
        pre_exp = ((z - z.T)**2).sum(axis=1)
        return np.exp(-(pre_exp / length))

    @staticmethod
    def _mmd(x, y, rbf_width):
        n = len(x)
        m = len(y)
        z = np.concatenate([x, y])
        k = DistComparison._gaussian_kernel(z, rbf_width)

        kxx = k[0:n, 0:n]
        kxy = k[n:, 0:n]
        kyy = k[n:, n:]

        return (kxx.sum() / (n**2)) - (2 * kxy.sum() / (n * m)) + (kyy.sum() /
                                                                   (m**2))

    @staticmethod
    def mmd_test(x, y):
        rbf_width = DistComparison._optimal_kernel_width([x, y]) / 4.0
        mmd_array = DistComparison._mmd(x, y, rbf_width)
        n_samples = min([500, x.shape[0], y.shape[0]])
        null_dist = DistComparison._mmd_null(x, y, rbf_width, n_samples)
        p_value = (null_dist[:, None] > mmd_array).sum() / float(n_samples)

        return mmd_array, p_value

    @staticmethod
    def _mmd_null(x, y, rbf_width, n_samples):
        s = [False for _ in range(n_samples)]
        z = np.concatenate([x, y])
        for i in range(n_samples):
            np.random.shuffle(z)
            s[i] = DistComparison._mmd(z[0:len(x)], z[len(x):], rbf_width)
        s = np.array(s)
        return s

    @staticmethod
    def _properties_test_statistics(property_list_1, property_list_2):
        """
        Preforms a range of statistical tests of the property distributions and returns a dictionary with the statistics. 
        Returns:

        """

        # Mutual information, continuous form from https://gist.github.com/GaelVaroquaux/ead9898bd3c973c40429
        mi = DistComparison._mutual_information_2d(property_list_1,
                                                   property_list_2)

        # Kolmogorov-Smirnov
        ks = ks_2samp(property_list_1, property_list_2)

        # Anderson-Darling
        ad = anderson_ksamp([property_list_1, property_list_2])

        # maximum mean discrepancy, maybe make it optional and then use
        # the linear implementation in shogun to do this, ore use medium pairwise squared distance to
        # estimate the kernel bandwidth as in
        # https://github.com/dougalsutherland/mmd/blob/master/examples/mmd%20regression%20example.ipynb
        logger.warning(
            'the current implementation of mmd is not optimal, '
            'a optional support for shogon (selects optimal kernel, linear algorithm) '
            'will be implemented in a further release')

        mmd, mmd_p = DistComparison.mmd_test(
            property_list_1.reshape(-1, 1), property_list_2.reshape(-1, 1))

        # Mann-Whitney U
        mwu = mannwhitneyu(property_list_1, property_list_2)

        # t-test
        ttest = ttest_ind(property_list_1, property_list_2)

        result_dict = {
            'mutual_information': mi,
            'ks_statistic': ks[0],
            'ks_p_value': ks[1],
            'mann_whitney_u_statistic': mwu[0],
            'mann_whitney_u_p_value': mwu[1],
            'mmd_statistic': mmd,
            'mmd_p_value': mmd_p,
            'ttest_statistic': ttest[0],
            'ttest_p_value': ttest[1],
            'anderson_darling_statistic': ad[0],
            'anderson_darling_p_value': ad[-1],
            'anderson_darling_critical_values': ad[1],
        }

        return result_dict

    def properties_test(self):
        """

        Returns:

        """
        if self.list_of_list_mode:
            # concurrently loop of the different feature columns.
            with concurrent.futures.ProcessPoolExecutor() as executor:
                logger.debug(
                    'looping over feature columns for properties statistics')
                out_dict = {}
                for i, results_dict in enumerate(
                        executor.map(
                            DistComparison._properties_test_statistics,
                            self.property_list_1, self.property_list_2)):
                    logger.debug('Creating statistics for %s',
                                 self.feature_names[i])
                    self.properties_statistics[self.
                                               feature_names[i]] = results_dict
                    out_dict[self.feature_names[i]] = results_dict

                mmd, mmd_p = DistComparison.mmd_test(
                    np.array(self.property_list_1),
                    np.array(self.property_list_2))
                overall_statistics = {
                    'mmd_statistic': mmd,
                    'mmd_p_value': mmd_p,
                }
                self.properties_statistics['global'] = overall_statistics
                out_dict['global'] = overall_statistics
            return out_dict
        else:
            out_dict = {}
            results_dict = DistComparison._properties_test_statistics(
                self.property_list_1, self.property_list_2)
            self.properties_statistics[self.feature_names] = results_dict
            out_dict[self.feature_names] = results_dict
            return out_dict


class DistExampleComparison():
    def __init__(self, structure_list, file):
        self.structure_list = structure_list
        self.file = file

    def __len__(self):
        return len(self.structure_list)

    def __repr__(self):
        return f'DistExampleComparison with file {self.file}'

    @classmethod
    def from_folder_and_file(class_object, folder, file, extension='cif'):
        sl = get_structure_list(folder, extension)
        return class_object(sl, file)

    @staticmethod
    def property_based_distances_histogram(
            property_list: list) -> pd.DataFrame:
        ...

    def property_based_distances_clustered(
            self, property_list: list) -> pd.DataFrame:
        """
        Compares other structure to
            - lowest, highest, median, mean and random structure from structure list

        Returns the RMSD and the graph jaccard distance between other structure and the
        aforementioned structures.

        This can be useful in case there is a direct link between structure and property
        and we want to make sure that a structure is not to dissimilar from the set of structures
        in structure_list.
        Args:
            property_list:

        Returns:

        """

        median_index = closest_index(property_list, np.median(property_list))
        mean_index = closest_index(property_list, np.mean(property_list))
        random_index = np.random.randint(0, len(self.structure_list))
        q25_index = closest_index(property_list,
                                  np.quantile(property_list, 0.25))
        q75_index = closest_index(property_list,
                                  np.quantile(property_list, 0.75))

        lowest_structure = Structure.from_file(
            self.structure_list[np.argmin(property_list)])
        highest_structure = Structure.from_file(
            self.structure_list[np.argmax(property_list)])
        median_structure = Structure.from_file(
            self.structure_list[median_index])
        mean_structure = Structure.from_file(self.structure_list[mean_index])
        random_structure = Structure.from_file(
            self.structure_list[random_index])
        q25_structure = Structure.from_file(self.structure_list[q25_index])
        q75_structure = Structure.from_file(self.structure_list[q75_index])
        other_structure = Structure.from_file(self.file)

        nn_strategy = JmolNN()

        lowest_structure_graph = StructureGraph.with_local_env_strategy(
            lowest_structure, nn_strategy)
        highest_structure_graph = StructureGraph.with_local_env_strategy(
            highest_structure, nn_strategy)
        median_structure_graph = StructureGraph.with_local_env_strategy(
            median_structure, nn_strategy)
        mean_structure_graph = StructureGraph.with_local_env_strategy(
            mean_structure, nn_strategy)
        random_structure_graph = StructureGraph.with_local_env_strategy(
            random_structure, nn_strategy)
        q25_structure_graph = StructureGraph.with_local_env_strategy(
            q25_structure, nn_strategy)
        q75_structure_graph = StructureGraph.with_local_env_strategy(
            q75_structure, nn_strategy)
        other_structure_graph = StructureGraph.with_local_env_strategy(
            other_structure, nn_strategy)

        distances = {
            'mean_rmsd':
            get_rmsd(other_structure, mean_structure),
            'highest_rmsd':
            get_rmsd(other_structure, highest_structure),
            'lowest_rmsd':
            get_rmsd(other_structure, lowest_structure),
            'median_rmsd':
            get_rmsd(other_structure, median_structure),
            'random_rmsd':
            get_rmsd(other_structure, random_structure),
            'q25_rmsd':
            get_rmsd(other_structure, q25_structure),
            'q75_rmsd':
            get_rmsd(other_structure, q75_structure),
            'mean_jaccard':
            other_structure_graph.diff(mean_structure_graph,
                                       strict=False)['diff'],
            'highest_jaccard':
            other_structure_graph.diff(highest_structure_graph,
                                       strict=False)['diff'],
            'lowest_jaccard':
            other_structure_graph.diff(lowest_structure_graph,
                                       strict=False)['diff'],
            'median_jaccard':
            other_structure_graph.diff(median_structure_graph,
                                       strict=False)['diff'],
            'random_jaccard':
            other_structure_graph.diff(random_structure_graph,
                                       strict=False)['diff'],
            'q25_jaccard':
            other_structure_graph.diff(q25_structure_graph,
                                       strict=False)['diff'],
            'q75_jaccard':
            other_structure_graph.diff(q75_structure_graph,
                                       strict=False)['diff'],
        }

        return pd.DataFrame(distances)


def fingerprint_based_distances(fingerprint_list, other_fingerprint,
                                k=8) -> pd.DataFrame:
    """
    This comparator performes clustering in the fingerprint space and compares the distance
    of the other fingerprint to all clusters. This could be useful in a ML model to check
    if the model is performing interpolation (probably trustworthy) or extrapolation (random guessing might be involved).

    Returns a DataFrame with different distances between the clusters and the other fingerprint.

    :param fingerprint_list:
    :param other_fingerprint:
    :param k:
    :return:
    """
    if len(fingerprint_list) < k:
        logger.warning(
            'The number of points is smaller than the number of clusters, will reduce number of cluster'
        )
        k = len(fingerprint_list) - 1

    fingerprint_array = np.array(fingerprint_list)
    other_fingerprint_array = np.array(other_fingerprint)
    assert len(fingerprint_array.shape) == len(
        other_fingerprint_array.shape)  # they should have same dimensionality
    if len(fingerprint_array.shape) == 1:
        fingerprint_array = fingerprint_array.reshape(-1, 1)
        other_fingerprint_array = other_fingerprint_array.reshape(-1, 1)

    # Perform knn clustering in fingerprint space
    kmeans = KMeans(n_clusters=k, random_state=0).fit(fingerprint_array)

    cluster_centers = kmeans.cluster_centers_

    distances = []
    for cluster_center in cluster_centers:
        distance_dict = {
            'tanimoto':
            tanimoto_distance(cluster_center, other_fingerprint_array),
            'euclidean':
            distance.euclidean(cluster_center, other_fingerprint_array),
            'correlation':
            distance.correlation(cluster_center, other_fingerprint_array),
            'manhattan':
            distance.cityblock(cluster_center, other_fingerprint_array)
        }
        distances.append(distance_dict)

    return pd.DataFrame(distances)
