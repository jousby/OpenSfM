# pyre-strict
"""Tools to extract features."""

import logging
import time
from typing import Any, BinaryIO, Dict, List, Optional, Tuple, Union

import cv2
import numpy as np
from numpy.typing import NDArray
from opensfm import context, pyfeatures


logger: logging.Logger = logging.getLogger(__name__)


class SemanticData:
    segmentation: NDArray
    instances: Optional[NDArray]
    labels: List[Dict[str, Any]]

    def __init__(
        self,
        segmentation: NDArray,
        instances: Optional[NDArray],
        labels: List[Dict[str, Any]],
    ) -> None:
        self.segmentation = segmentation
        self.instances = instances
        self.labels = labels

    def has_instances(self) -> bool:
        return self.instances is not None

    def mask(self, mask: NDArray) -> "SemanticData":
        try:
            segmentation = self.segmentation[mask]
            instances = self.instances
            if instances is not None:
                instances = instances[mask]
        except IndexError:
            logger.error(
                f"Invalid mask array of dtype {mask.dtype}, shape {mask.shape}: {mask}"
            )
            raise

        return SemanticData(segmentation, instances, self.labels)


class FeaturesData:
    points: NDArray
    descriptors: Optional[NDArray]
    colors: NDArray
    semantic: Optional[SemanticData]
    depths: Optional[NDArray]  # New field. This field is not serialized yet

    FEATURES_VERSION: int = 3
    FEATURES_HEADER: str = "OPENSFM_FEATURES_VERSION"

    def __init__(
        self,
        points: NDArray,
        descriptors: Optional[NDArray],
        colors: NDArray,
        semantic: Optional[SemanticData],
        depths: Optional[NDArray] = None,
    ) -> None:
        self.points = points
        self.descriptors = descriptors
        self.colors = colors
        self.semantic = semantic
        self.depths = depths

    def get_segmentation(self) -> Optional[NDArray]:
        semantic = self.semantic
        if not semantic:
            return None
        if semantic.segmentation is not None:
            return semantic.segmentation
        return None

    def has_instances(self) -> bool:
        semantic = self.semantic
        if not semantic:
            return False
        return semantic.instances is not None

    def mask(self, mask: NDArray) -> "FeaturesData":
        if self.semantic:
            masked_semantic = self.semantic.mask(mask)
        else:
            masked_semantic = None
        return FeaturesData(
            self.points[mask],
            self.descriptors[mask] if self.descriptors is not None else None,
            self.colors[mask],
            masked_semantic,
            self.depths[mask] if self.depths is not None else None,
        )

    def save(self, fileobject: Union[str, BinaryIO], config: Dict[str, Any]) -> None:
        """Save features from file (path like or file object like)"""
        feature_type = config["feature_type"].upper()
        if (
            (
                feature_type == "AKAZE"
                and config["akaze_descriptor"] in ["MLDB_UPRIGHT", "MLDB"]
            )
            or (feature_type == "HAHOG" and config["hahog_normalize_to_uchar"])
            or (feature_type == "ORB")
        ):
            feature_data_type = np.uint8
        else:
            feature_data_type = np.float32
        descriptors = self.descriptors
        if descriptors is None:
            raise RuntimeError("No descriptors found, cannot save features data.")
        semantic = self.semantic
        if semantic:
            instances = semantic.instances
            np.savez_compressed(
                fileobject,
                points=self.points.astype(np.float32),
                descriptors=descriptors.astype(feature_data_type),
                colors=self.colors,
                segmentations=semantic.segmentation.astype(np.uint8),
                instances=instances.astype(np.int16) if instances is not None else [],
                segmentation_labels=np.array(semantic.labels).astype(str),
                OPENSFM_FEATURES_VERSION=self.FEATURES_VERSION,
            )
        else:
            np.savez_compressed(
                fileobject,
                points=self.points.astype(np.float32),
                descriptors=descriptors.astype(feature_data_type),
                colors=self.colors,
                segmentations=[],
                instances=[],
                segmentation_labels=[],
                OPENSFM_FEATURES_VERSION=self.FEATURES_VERSION,
            )

    @classmethod
    def from_file(
        cls, fileobject: Union[str, BinaryIO], config: Dict[str, Any]
    ) -> "FeaturesData":
        """Load features from file (path like or file object like)"""
        s = np.load(fileobject, allow_pickle=False)
        version = cls._features_file_version(s)
        return getattr(cls, "_from_file_v%d" % version)(s, config)

    @classmethod
    def _features_file_version(cls, obj: Dict[str, Any]) -> int:
        """Retrieve features file version. Return 0 if none"""
        if cls.FEATURES_HEADER in obj:
            return obj[cls.FEATURES_HEADER]
        else:
            return 0

    @classmethod
    def _from_file_v0(
        cls, data: Dict[str, NDArray], config: Dict[str, Any]
    ) -> "FeaturesData":
        """Base version of features file

        Scale (desc[2]) set to reprojection_error_sd by default (legacy behaviour)
        """
        feature_type = config["feature_type"]
        if feature_type == "HAHOG" and config["hahog_normalize_to_uchar"]:
            descriptors = data["descriptors"].astype(np.float32)
        else:
            descriptors = data["descriptors"]
        points = data["points"]
        points[:, 2:3] = config["reprojection_error_sd"]
        return FeaturesData(points, descriptors, data["colors"].astype(float), None)

    @classmethod
    def _from_file_v1(
        cls, data: Dict[str, NDArray], config: Dict[str, Any]
    ) -> "FeaturesData":
        """Version 1 of features file

        Scale is not properly set higher in the pipeline, default is gone.
        """
        feature_type = config["feature_type"]
        if feature_type == "HAHOG" and config["hahog_normalize_to_uchar"]:
            descriptors = data["descriptors"].astype(np.float32)
        else:
            descriptors = data["descriptors"]
        return FeaturesData(
            data["points"], descriptors, data["colors"].astype(float), None
        )

    @classmethod
    def _from_file_v2(
        cls,
        data: Dict[str, Any],
        config: Dict[str, Any],
    ) -> "FeaturesData":
        """
        Version 2 of features file

        Added segmentation, instances and segmentation labels. This version has been introduced at
        e5da878bea455a1e4beac938cb30b796acfe3c8c, but has been superseded by version 3 as this version
        uses 'allow_pickle=True' which isn't safe (RCE vulnerability)
        """
        feature_type = config["feature_type"]
        if feature_type == "HAHOG" and config["hahog_normalize_to_uchar"]:
            descriptors = data["descriptors"].astype(np.float32)
        else:
            descriptors = data["descriptors"]

        # luckily, because os lazy loading, we can still load 'segmentations' and 'instances' ...
        pickle_message = (
            "Cannot load {} as these were generated with "
            "version 2 which isn't supported anymore because of RCE vulnerablity."
            "Please consider re-extracting features data for this dataset"
        )
        try:
            has_segmentation = (data["segmentations"] != None).all()
            has_instances = (data["instances"] != None).all()
        except ValueError:
            logger.warning(pickle_message.format("segmentations and instances"))
            has_segmentation, has_instances = False, False

        # ... whereas 'labels' can't be loaded anymore, as it is a plain 'list' object. Not an
        # issue since these labels are used for description only and not actual filtering.
        try:
            labels = data["segmentation_labels"]
        except ValueError:
            logger.warning(pickle_message.format("labels"))
            labels = []

        if has_segmentation or has_instances:
            semantic_data = SemanticData(
                data["segmentations"] if has_segmentation else None,
                data["instances"] if has_instances else None,
                labels,
            )
        else:
            semantic_data = None
        return FeaturesData(
            data["points"], descriptors, data["colors"].astype(float), semantic_data
        )

    @classmethod
    def _from_file_v3(
        cls,
        data: Dict[str, Any],
        config: Dict[str, Any],
    ) -> "FeaturesData":
        """
        Version 3 of features file

        Same as version 2, except that
        """
        feature_type = config["feature_type"]
        if feature_type == "HAHOG" and config["hahog_normalize_to_uchar"]:
            descriptors = data["descriptors"].astype(np.float32)
        else:
            descriptors = data["descriptors"]

        has_segmentation = len(data["segmentations"]) > 0
        has_instances = len(data["instances"]) > 0

        if has_segmentation or has_instances:
            semantic_data = SemanticData(
                data["segmentations"] if has_segmentation else None,
                data["instances"] if has_instances else None,
                data["segmentation_labels"],
            )
        else:
            semantic_data = None
        return FeaturesData(
            data["points"], descriptors, data["colors"].astype(float), semantic_data
        )


def resized_image(image: NDArray, max_size: int) -> NDArray:
    """Resize image to feature_process_size."""
    h, w = image.shape[:2]
    size = max(w, h)
    if 0 < max_size < size:
        dsize = w * max_size // size, h * max_size // size
        return cv2.resize(image, dsize=dsize, interpolation=cv2.INTER_AREA)
    else:
        return image


def root_feature(desc: NDArray, l2_normalization: bool = False) -> NDArray:
    if l2_normalization:
        s2 = np.linalg.norm(desc, axis=1)
        desc = (desc.T / s2).T
    s = np.sum(desc, 1)
    desc = np.sqrt(desc.T / s).T
    return desc


def root_feature_surf(
    desc: NDArray, l2_normalization: bool = False, partial: bool = False
) -> NDArray:
    """
    Experimental square root mapping of surf-like feature, only work for 64-dim surf now
    """
    if desc.shape[1] == 64:
        if l2_normalization:
            s2 = np.linalg.norm(desc, axis=1)
            desc = (desc.T / s2).T
        if partial:
            ii = np.array([i for i in range(64) if (i % 4 == 2 or i % 4 == 3)])
        else:
            ii = np.arange(64)
        desc_sub = np.abs(desc[:, ii])
        desc_sub_sign = np.sign(desc[:, ii])
        # s_sub = np.sum(desc_sub, 1)  # This partial normalization gives slightly better results for AKAZE surf
        s_sub = np.sum(np.abs(desc), 1)
        desc_sub = np.sqrt(desc_sub.T / s_sub).T
        desc[:, ii] = desc_sub * desc_sub_sign
    return desc


def normalized_image_coordinates(
    pixel_coords: NDArray, width: int, height: int
) -> NDArray:
    size = max(width, height)
    p = np.empty((len(pixel_coords), 2))
    p[:, 0] = (pixel_coords[:, 0] + 0.5 - width / 2.0) / size
    p[:, 1] = (pixel_coords[:, 1] + 0.5 - height / 2.0) / size
    return p


def denormalized_image_coordinates(
    norm_coords: NDArray, width: int, height: int
) -> NDArray:
    size = max(width, height)
    p = np.empty((len(norm_coords), 2))
    p[:, 0] = norm_coords[:, 0] * size - 0.5 + width / 2.0
    p[:, 1] = norm_coords[:, 1] * size - 0.5 + height / 2.0
    return p


def normalize_features(
    points: NDArray, desc: NDArray, colors: NDArray, width: int, height: int
) -> Tuple[
    NDArray,
    NDArray,
    NDArray,
]:
    """Normalize feature coordinates and size."""
    points[:, :2] = normalized_image_coordinates(points[:, :2], width, height)
    points[:, 2:3] /= max(width, height)
    return points, desc, colors


def _in_mask(point: NDArray, width: int, height: int, mask: NDArray) -> bool:
    """Check if a point is inside a binary mask."""
    u = mask.shape[1] * (point[0] + 0.5) / width
    v = mask.shape[0] * (point[1] + 0.5) / height
    return mask[int(v), int(u)] != 0


def extract_features_sift(
    image: NDArray, config: Dict[str, Any], features_count: int
) -> Tuple[NDArray, NDArray]:
    sift_edge_threshold = config["sift_edge_threshold"]
    sift_peak_threshold = float(config["sift_peak_threshold"])
    sift_nfeatures = config["sift_nfeatures"]
    sift_octave_layers = config["sift_octave_layers"]
    sift_sigma = float(config["sift_sigma"])
    while True:
        logger.debug("Computing sift with threshold {0}".format(sift_peak_threshold))
        t = time.time()
        # SIFT support is in cv2 main from version 4.4.0
        if context.OPENCV44 or context.OPENCV5:
            detector = cv2.SIFT_create(
                nfeatures=sift_nfeatures,
                nOctaveLayers=sift_octave_layers,
                contrastThreshold=sift_peak_threshold,
                edgeThreshold=sift_edge_threshold,
                sigma=sift_sigma,
            )
            descriptor = detector
        elif context.OPENCV3:
            detector = cv2.xfeatures2d.SIFT_create(
                nfeatures=sift_nfeatures,
                nOctaveLayers=sift_octave_layers,
                contrastThreshold=sift_peak_threshold,
                edgeThreshold=sift_edge_threshold,
                sigma=sift_sigma,
            )
            descriptor = detector
        else:
            detector = cv2.FeatureDetector_create("SIFT")
            descriptor = cv2.DescriptorExtractor_create("SIFT")
            detector.setDouble("edgeThreshold", sift_edge_threshold)

        points = detector.detect(image)
        logger.debug("Found {0} points in {1}s".format(len(points), time.time() - t))
        if len(points) < features_count and sift_peak_threshold > 0.0001:
            sift_peak_threshold = (sift_peak_threshold * 2) / 3
            logger.debug("reducing threshold")
        else:
            logger.debug("done")
            break

    points, desc = descriptor.compute(image, points)

    if desc is not None:
        if config["feature_root"]:
            desc = root_feature(desc)
        points = np.array([(i.pt[0], i.pt[1], i.size, i.angle) for i in points])
    else:
        points = np.array(np.zeros((0, 3)))
        desc = np.array(np.zeros((0, 3)))
    return points, desc


def extract_features_surf(
    image: NDArray, config: Dict[str, Any], features_count: int
) -> Tuple[NDArray, NDArray]:
    surf_hessian_threshold = config["surf_hessian_threshold"]
    if context.OPENCV3:
        try:
            detector = cv2.xfeatures2d.SURF_create()
        except AttributeError as ae:
            if "no attribute 'xfeatures2d'" in str(ae):
                logger.error(
                    "OpenCV Contrib modules are required to extract SURF features"
                )
            raise
        descriptor = detector
        detector.setHessianThreshold(surf_hessian_threshold)
        detector.setNOctaves(config["surf_n_octaves"])
        detector.setNOctaveLayers(config["surf_n_octavelayers"])
        detector.setUpright(config["surf_upright"])
    else:
        detector = cv2.FeatureDetector_create("SURF")
        descriptor = cv2.DescriptorExtractor_create("SURF")
        detector.setDouble("hessianThreshold", surf_hessian_threshold)
        detector.setDouble("nOctaves", config["surf_n_octaves"])
        detector.setDouble("nOctaveLayers", config["surf_n_octavelayers"])
        detector.setInt("upright", config["surf_upright"])

    while True:
        logger.debug("Computing surf with threshold {0}".format(surf_hessian_threshold))
        t = time.time()
        if context.OPENCV3:
            detector.setHessianThreshold(surf_hessian_threshold)
        else:
            detector.setDouble(
                "hessianThreshold", surf_hessian_threshold
            )  # default: 0.04
        points = detector.detect(image)
        logger.debug("Found {0} points in {1}s".format(len(points), time.time() - t))
        if len(points) < features_count and surf_hessian_threshold > 0.0001:
            surf_hessian_threshold = (surf_hessian_threshold * 2) / 3
            logger.debug("reducing threshold")
        else:
            logger.debug("done")
            break

    points, desc = descriptor.compute(image, points)

    if desc is not None:
        if config["feature_root"]:
            desc = root_feature(desc)
        points = np.array([(i.pt[0], i.pt[1], i.size, i.angle) for i in points])
    else:
        points = np.array(np.zeros((0, 3)))
        desc = np.array(np.zeros((0, 3)))
    return points, desc


def akaze_descriptor_type(name: str) -> pyfeatures.AkazeDescriptorType:
    d = pyfeatures.AkazeDescriptorType.__dict__
    if name in d:
        return d[name]
    else:
        logger.debug("Wrong akaze descriptor type")
        return d["MSURF"]


def extract_features_akaze(
    image: NDArray, config: Dict[str, Any], features_count: int
) -> Tuple[NDArray, NDArray]:
    options = pyfeatures.AKAZEOptions()
    options.omax = config["akaze_omax"]
    akaze_descriptor_name = config["akaze_descriptor"]
    options.descriptor = akaze_descriptor_type(akaze_descriptor_name)
    options.descriptor_size = config["akaze_descriptor_size"]
    options.descriptor_channels = config["akaze_descriptor_channels"]
    options.dthreshold = config["akaze_dthreshold"]
    options.kcontrast_percentile = config["akaze_kcontrast_percentile"]
    options.use_isotropic_diffusion = config["akaze_use_isotropic_diffusion"]
    options.target_num_features = features_count
    options.use_adaptive_suppression = config["feature_use_adaptive_suppression"]

    logger.debug("Computing AKAZE with threshold {0}".format(options.dthreshold))
    t = time.time()
    points, desc = pyfeatures.akaze(image, options)
    logger.debug("Found {0} points in {1}s".format(len(points), time.time() - t))

    if config["feature_root"]:
        if akaze_descriptor_name in ["SURF_UPRIGHT", "MSURF_UPRIGHT"]:
            desc = root_feature_surf(desc, partial=True)
        elif akaze_descriptor_name in ["SURF", "MSURF"]:
            desc = root_feature_surf(desc, partial=False)
    points = points.astype(float)
    return points, desc


def extract_features_hahog(
    image: NDArray, config: Dict[str, Any], features_count: int
) -> Tuple[NDArray, NDArray]:
    t = time.time()
    points, desc = pyfeatures.hahog(
        image.astype(np.float32) / 255,  # VlFeat expects pixel values between 0, 1
        peak_threshold=config["hahog_peak_threshold"],
        edge_threshold=config["hahog_edge_threshold"],
        target_num_features=features_count,
    )

    if config["feature_root"]:
        desc = np.sqrt(desc)
        uchar_scaling = 362  # x * 512 < 256  =>  sqrt(x) * 362 < 256
    else:
        uchar_scaling = 512

    if config["hahog_normalize_to_uchar"]:
        # pyre-fixme[16]: `int` has no attribute `clip`.
        desc = (uchar_scaling * desc).clip(0, 255).round()

    logger.debug("Found {0} points in {1}s".format(len(points), time.time() - t))
    return points, desc


def extract_features_orb(
    image: NDArray, config: Dict[str, Any], features_count: int
) -> Tuple[NDArray, NDArray]:
    if context.OPENCV3:
        detector = cv2.ORB_create(nfeatures=features_count)
        descriptor = detector
    else:
        detector = cv2.FeatureDetector_create("ORB")
        descriptor = cv2.DescriptorExtractor_create("ORB")
        detector.setDouble("nFeatures", features_count)

    logger.debug("Computing ORB")
    t = time.time()
    points = detector.detect(image)

    points, desc = descriptor.compute(image, points)
    if desc is not None:
        points = np.array([(i.pt[0], i.pt[1], i.size, i.angle) for i in points])
    else:
        points = np.array(np.zeros((0, 3)))
        desc = np.array(np.zeros((0, 3)))

    logger.debug("Found {0} points in {1}s".format(len(points), time.time() - t))
    return points, desc


def extract_features(
    image: NDArray, config: Dict[str, Any], is_panorama: bool
) -> Tuple[NDArray, NDArray, NDArray]:
    """Detect features in a color or gray-scale image.

    The type of feature detected is determined by the ``feature_type``
    config option.

    The coordinates of the detected points are returned in normalized
    image coordinates.

    Parameters:
        - image: a color image with shape (h, w, 3) or
                 gray-scale image with (h, w) or (h, w, 1)
        - config: the configuration structure
        - is_panorama : if True, alternate settings are used for feature count and extraction size.

    Returns:
        tuple:
        - points: ``x``, ``y``, ``size`` and ``angle`` for each feature
        - descriptors: the descriptor of each feature
        - colors: the color of the center of each feature
    """
    extraction_size = (
        config["feature_process_size_panorama"]
        if is_panorama
        else config["feature_process_size"]
    )
    features_count = (
        config["feature_min_frames_panorama"]
        if is_panorama
        else config["feature_min_frames"]
    )

    assert image.ndim == 2 or image.ndim == 3 and image.shape[2] in [1, 3]
    assert image.shape[0] > 2 and image.shape[1] > 2
    assert np.issubdtype(image.dtype, np.uint8)

    image = resized_image(image, extraction_size)
    if image.ndim == 2:  # convert (h, w) to (h, w, 1)
        image = np.expand_dims(image, axis=2)

    # convert color to gray-scale if necessary
    if image.shape[2] == 3:
        image_gray = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
    else:
        image_gray = image
    feature_type = config["feature_type"].upper()
    if feature_type == "SIFT":
        points, desc = extract_features_sift(image_gray, config, features_count)
    elif feature_type == "SURF":
        points, desc = extract_features_surf(image_gray, config, features_count)
    elif feature_type == "AKAZE":
        points, desc = extract_features_akaze(image_gray, config, features_count)
    elif feature_type == "HAHOG":
        points, desc = extract_features_hahog(image_gray, config, features_count)
    elif feature_type == "ORB":
        points, desc = extract_features_orb(image_gray, config, features_count)
    else:
        raise ValueError(
            "Unknown feature type (must be SURF, SIFT, AKAZE, HAHOG or ORB)"
        )

    xs = points[:, 0].round().astype(int)
    ys = points[:, 1].round().astype(int)
    colors = image[ys, xs]
    if image.shape[2] == 1:
        colors = np.repeat(colors, 3).reshape((-1, 3))

    return normalize_features(points, desc, colors, image.shape[1], image.shape[0])


def build_flann_index(descriptors: NDArray, config: Dict[str, Any]) -> cv2.flann_Index:
    # FLANN_INDEX_LINEAR = 0
    FLANN_INDEX_KDTREE = 1
    FLANN_INDEX_KMEANS = 2
    # FLANN_INDEX_COMPOSITE = 3
    # FLANN_INDEX_KDTREE_SINGLE = 4
    # FLANN_INDEX_HIERARCHICAL = 5
    FLANN_INDEX_LSH = 6

    if descriptors.dtype.type is np.float32:
        algorithm_type = config["flann_algorithm"].upper()
        if algorithm_type == "KMEANS":
            FLANN_INDEX_METHOD = FLANN_INDEX_KMEANS
        elif algorithm_type == "KDTREE":
            FLANN_INDEX_METHOD = FLANN_INDEX_KDTREE
        else:
            raise ValueError("Unknown flann algorithm type " "must be KMEANS, KDTREE")
        flann_params = {
            "algorithm": FLANN_INDEX_METHOD,
            "branching": config["flann_branching"],
            "iterations": config["flann_iterations"],
            "tree": config["flann_tree"],
        }
    elif descriptors.dtype.type is np.uint8:
        flann_params = {
            "algorithm": FLANN_INDEX_LSH,
            "table_number": 10,
            "key_size": 24,
            "multi_probe_level": 1,
        }
    else:
        raise ValueError(
            f"FLANN isn't supported for feature type {descriptors.dtype.type}."
        )

    return context.flann_Index(descriptors, flann_params)
