# pyre-strict
import functools
import math
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

import numpy as np
import opensfm.synthetic_data.synthetic_dataset as sd
import opensfm.synthetic_data.synthetic_generator as sg
import opensfm.synthetic_data.synthetic_metrics as sm
from numpy.typing import NDArray
from opensfm import geo, pygeometry, pymap, types
from opensfm.reconstruction_helpers import exif_to_metadata


def get_camera(
    type: str, id: str, focal: float, k1: float, k2: float
) -> pygeometry.Camera:
    camera: Optional[pygeometry.Camera] = None
    if type == "perspective":
        camera = pygeometry.Camera.create_perspective(focal, k1, k2)
    elif type == "fisheye":
        camera = pygeometry.Camera.create_fisheye(focal, k1, k2)
    elif type == "spherical":
        camera = pygeometry.Camera.create_spherical()
    else:
        raise RuntimeError(f"Invalid camera type {type}")

    camera.id = id

    camera.height = 1600
    camera.width = 2000
    return camera


def get_scene_generator(
    type: str, length: float, **kwargs: Any
) -> Callable[[float], NDArray]:
    generator = None
    if type == "circle":
        generator = functools.partial(sg.ellipse_generator, length, length)
    if type == "ellipse":
        ellipse_ratio = 2
        generator = functools.partial(
            sg.ellipse_generator, length, length / ellipse_ratio
        )
    if type == "line":
        center_x = kwargs.get("center_x", 0)
        center_y = kwargs.get("center_y", 0)
        transpose = kwargs.get("transpose", True)
        generator = functools.partial(
            sg.line_generator, length, center_x, center_y, transpose
        )
    assert generator
    return generator


def camera_pose(position: NDArray, lookat: NDArray, up: NDArray) -> pygeometry.Pose:
    """
    Pose from position and look at direction

    >>> position = [1.0, 2.0, 3.0]
    >>> lookat = [0., 10.0, 2.0]
    >>> up = [0.0, 0.0, 1.0]
    >>> pose = camera_pose(position, lookat, up)
    >>> np.allclose(pose.get_origin(), position)
    True
    """

    def normalized(x: NDArray) -> NDArray:
        return x / np.linalg.norm(x)

    ez = normalized(np.array(lookat) - np.array(position))
    ex = normalized(np.cross(ez, up))
    ey = normalized(np.cross(ez, ex))
    pose = pygeometry.Pose()
    pose.set_rotation_matrix(np.array([ex, ey, ez]))
    pose.set_origin(position)
    return pose


class SyntheticScene:
    def get_reconstruction(self) -> types.Reconstruction:
        raise NotImplementedError()


class SyntheticCubeScene(SyntheticScene):
    """Scene consisting of cameras looking at point in a cube."""

    def __init__(self, num_cameras: int, num_points: int, noise: float) -> None:
        self.reconstruction = types.Reconstruction()
        self.cameras: Dict[str, pygeometry.Camera] = {}
        for i in range(num_cameras):
            camera = camera = pygeometry.Camera.create_perspective(0.9, -0.1, 0.01)
            camera.id = "camera%04d" % i
            camera.height = 600
            camera.width = 800
            self.cameras[camera.id] = camera

        self.reconstruction.cameras = self.cameras

        r = 2.0
        for i in range(num_cameras):
            phi = np.random.rand() * math.pi
            theta = np.random.rand() * 2.0 * math.pi
            x = r * np.sin(theta) * np.cos(phi)
            y = r * np.sin(theta) * np.sin(phi)
            z = r * np.cos(theta)
            position = np.array([x, y, z])

            alpha = np.random.rand()
            lookat = np.array([0.0, 0, 0])
            up = np.array([alpha * 0.2, alpha * 0.2, 1.0])
            shot_id = "shot%04d" % i
            camera_id = "camera%04d" % i
            pose = camera_pose(position, lookat, up)
            self.reconstruction.create_shot(
                shot_id, camera_id, pose, rig_camera_id=None, rig_instance_id=None
            )

        points = np.random.rand(num_points, 3) - [0.5, 0.5, 0.5]
        for i, p in enumerate(points):
            point_id = "point" + str(i)
            pt = self.reconstruction.create_point(point_id, p)
            pt.color = np.array([100, 100, 20])

    def get_reconstruction(self) -> types.Reconstruction:
        reconstruction = types.Reconstruction()
        # Copy our original reconstruction
        # since we do not want to modify the reference
        reconstruction.cameras = self.cameras
        for shot in self.reconstruction.shots.values():
            reconstruction.create_shot(
                shot.id,
                shot.camera.id,
                shot.pose,
                rig_camera_id=None,
                rig_instance_id=None,
            )
        for point in self.reconstruction.points.values():
            pt = reconstruction.create_point(point.id, point.coordinates)
            pt.color = point.color
        return reconstruction


class SyntheticStreetScene(SyntheticScene):
    """Scene consisting in a virtual street extruded along some
    parametric shape (line, ellipse), with camera placed along
    the shape.
    """

    generator: Optional[Callable[[float], NDArray]]
    wall_points: Optional[NDArray]
    floor_points: Optional[NDArray]
    shot_ids: List[List[str]]
    cameras: List[List[pygeometry.Camera]]
    instances_positions: List[NDArray]
    instances_rotations: List[NDArray]
    rig_instances: List[List[List[Tuple[str, str]]]]
    rig_cameras: List[List[pymap.RigCamera]]
    width: float

    def __init__(
        self,
        generator: Optional[Callable[[float], NDArray]],
        reference: Optional[geo.TopocentricConverter] = None,
    ) -> None:
        self.generator = generator
        self.reference = reference
        self.wall_points: Optional[NDArray] = None
        self.floor_points: Optional[NDArray] = None
        self.shot_ids = []
        self.cameras = []
        self.instances_positions: List[NDArray] = []
        self.instances_rotations: List[NDArray] = []
        self.rig_instances = []
        self.rig_cameras = []
        self.width = 0.0

    def combine(self, other_scene: "SyntheticStreetScene") -> "SyntheticStreetScene":
        combined_scene = SyntheticStreetScene(None)
        combined_scene.wall_points = np.concatenate(
            # pyre-fixme[6]: For 1st argument expected `Union[_SupportsArray[dtype[ty...
            (self.wall_points, other_scene.wall_points)
        )
        combined_scene.floor_points = np.concatenate(
            # pyre-fixme[6]: For 1st argument expected `Union[_SupportsArray[dtype[ty...
            (self.floor_points, other_scene.floor_points)
        )
        combined_scene.cameras = self.cameras + other_scene.cameras
        combined_scene.instances_positions = (
            self.instances_positions + other_scene.instances_positions
        )
        combined_scene.instances_rotations = (
            self.instances_rotations + other_scene.instances_rotations
        )
        combined_scene.rig_instances = self.rig_instances + other_scene.rig_instances
        combined_scene.rig_cameras = self.rig_cameras + other_scene.rig_cameras
        combined_scene.shot_ids = self.shot_ids + other_scene.shot_ids

        shift = 0
        for subshots in combined_scene.shot_ids:
            for i in range(len(subshots)):
                subshots[i] = f"Shot {i+shift:04d}"
            shift += len(subshots)
        return combined_scene

    def add_street(
        self, points_count: int, height: float, width: float
    ) -> "SyntheticStreetScene":
        generator = self.generator
        assert generator
        self.wall_points, self.floor_points = sg.generate_street(
            sg.samples_generator_random_count(int(points_count // 3)),
            generator,
            height,
            width,
        )
        self.width = width
        return self

    def perturb_walls(self, walls_pertubation: List[float]) -> "SyntheticStreetScene":
        wall_points = self.wall_points
        assert wall_points is not None
        sg.perturb_points(wall_points, walls_pertubation)
        return self

    def perturb_floor(self, floor_pertubation: List[float]) -> "SyntheticStreetScene":
        floor_points = self.floor_points
        assert floor_points is not None
        sg.perturb_points(floor_points, floor_pertubation)
        return self

    def set_terrain_hill(
        self, height: float, radius: float, repeated: bool
    ) -> "SyntheticStreetScene":
        if not repeated:
            self._set_terrain_hill_single(height, radius)
        else:
            self._set_terrain_hill_repeated(height, radius)
        return self

    def _set_terrain_hill_single(self, height: float, radius: float) -> None:
        wall_points, floor_points = self.wall_points, self.floor_points
        assert wall_points is not None and floor_points is not None
        wall_points[:, 2] += height * np.exp(
            -0.5 * np.linalg.norm(wall_points[:, :2], axis=1) ** 2 / radius**2
        )
        floor_points[:, 2] += height * np.exp(
            -0.5 * np.linalg.norm(floor_points[:, :2], axis=1) ** 2 / radius**2
        )

        for positions in self.instances_positions:
            for position in positions:
                position[2] += height * np.exp(
                    -0.5
                    * np.linalg.norm((position[0] ** 2 + position[1] ** 2) / radius**2)
                )

    def _set_terrain_hill_repeated(self, height: float, radius: float) -> None:
        wall_points, floor_points = self.wall_points, self.floor_points
        assert wall_points is not None and floor_points is not None
        wall_points[:, 2] += height * np.sin(
            np.linalg.norm(wall_points[:, :2], axis=1) / radius
        )
        floor_points[:, 2] += height * np.sin(
            np.linalg.norm(floor_points[:, :2], axis=1) / radius
        )

        for positions in self.instances_positions:
            for position in positions:
                position[2] += height * np.sin(
                    math.sqrt(position[0] ** 2 + position[1] ** 2) / radius
                )

    def add_camera_sequence(
        self,
        camera: pygeometry.Camera,
        length: float,
        height: float,
        interval: float,
        position_noise: List[float],
        rotation_noise: float,
        positions_shift: Optional[List[float]] = None,
        end: Optional[float] = None,
    ) -> "SyntheticStreetScene":
        default_noise_interval = 0.25 * interval
        actual_end = length if end is None else end
        generator = self.generator
        assert generator
        positions, rotations = sg.generate_cameras(
            sg.samples_generator_interval(
                length, actual_end, interval, default_noise_interval
            ),
            generator,
            height,
        )
        sg.perturb_points(positions, position_noise)
        sg.perturb_rotations(rotations, rotation_noise)
        if positions_shift:
            positions += np.array(positions_shift)

        shift = 0 if len(self.shot_ids) == 0 else sum(len(s) for s in self.shot_ids)
        new_shot_ids = [f"Shot {shift+i:04d}" for i in range(len(positions))]
        self.shot_ids.append(new_shot_ids)
        self.cameras.append([camera])

        rig_camera = pymap.RigCamera(pygeometry.Pose(), camera.id)
        self.rig_cameras.append([rig_camera])

        rig_instances = []
        for shot_id in new_shot_ids:
            rig_instances.append([(shot_id, camera.id)])
        self.rig_instances.append(rig_instances)
        self.instances_positions.append(positions)
        self.instances_rotations.append(rotations)

        return self

    def add_rig_camera_sequence(
        self,
        cameras: List[pygeometry.Camera],
        relative_positions: List[List[float]],
        relative_rotations: List[List[float]],
        length: float,
        height: float,
        interval: float,
        position_noise: List[float],
        rotation_noise: float,
        end: Optional[float] = None,
    ) -> "SyntheticStreetScene":
        default_noise_interval = 0.25 * interval
        actual_end = length if end is None else end
        generator = self.generator
        assert generator
        instances_positions, instances_rotations = sg.generate_cameras(
            sg.samples_generator_interval(
                length, actual_end, interval, default_noise_interval
            ),
            generator,
            height,
        )
        sg.perturb_points(instances_positions, position_noise)
        sg.perturb_rotations(instances_rotations, rotation_noise)

        shift = sum(len(s) for s in self.shot_ids)
        shots_ids_per_camera = []
        for j, (rig_camera_p, rig_camera_r) in enumerate(
            zip(relative_positions, relative_rotations)
        ):
            # pyre-fixme[6]: For 1st argument expected `ndarray` but got `List[float]`.
            pose_rig_camera = pygeometry.Pose(rig_camera_r)
            # pyre-fixme[6]: For 1st argument expected `ndarray` but got `List[float]`.
            pose_rig_camera.set_origin(rig_camera_p)

            rotations = []
            positions = []
            for instance_p, instance_r in zip(instances_positions, instances_rotations):
                pose_instance = pygeometry.Pose(instance_r)
                pose_instance.set_origin(instance_p)
                composed = pose_rig_camera.compose(pose_instance)
                rotations.append(composed.rotation)
                positions.append(composed.get_origin())

            camera_shot_ids = []
            for i in range(len(positions)):
                shot_index = i * len(relative_positions) + j
                camera_shot_ids.append(f"Shot {shift+shot_index:04d}")
            shots_ids_per_camera.append(camera_shot_ids)
        self.cameras.append(cameras)
        self.shot_ids += shots_ids_per_camera

        rig_camera_ids = []
        rig_cameras = []
        rig_camera_id_shift = sum(len(s) for s in self.rig_cameras)
        for i, (rig_camera_p, rig_camera_r) in enumerate(
            zip(relative_positions, relative_rotations)
        ):
            # pyre-fixme[6]: For 1st argument expected `ndarray` but got `List[float]`.
            pose_rig_camera = pygeometry.Pose(rig_camera_r)
            # pyre-fixme[6]: For 1st argument expected `ndarray` but got `List[float]`.
            pose_rig_camera.set_origin(rig_camera_p)
            rig_camera_id = f"RigCamera {rig_camera_id_shift + i}"
            rig_camera = pymap.RigCamera(pose_rig_camera, rig_camera_id)
            rig_camera_ids.append(rig_camera_id)
            rig_cameras.append(rig_camera)
        self.rig_cameras.append(rig_cameras)

        rig_instances: List[List[Tuple[str, str]]] = []
        for i in range(len(instances_positions)):
            instance = []
            for j in range(len(shots_ids_per_camera)):
                instance.append((shots_ids_per_camera[j][i], rig_camera_ids[j]))
            rig_instances.append(instance)
        self.rig_instances.append(rig_instances)
        self.instances_positions.append(instances_positions)
        self.instances_rotations.append(instances_rotations)

        return self

    def get_reconstruction(self) -> types.Reconstruction:
        floor_color = [120, 90, 10]
        wall_color = [10, 90, 130]

        return sg.create_reconstruction(
            # pyre-fixme[6]: For 1st argument expected `List[ndarray[typing.Any,
            #  typing.Any]]` but got `ndarray[typing.Any, dtype[typing.Any]]`.
            points=np.asarray([self.floor_points, self.wall_points], dtype=object),
            # pyre-fixme[6]: For 2nd argument expected `List[ndarray[typing.Any,
            #  typing.Any]]` but got `ndarray[typing.Any, dtype[typing.Any]]`.
            colors=np.asarray([floor_color, wall_color]),
            cameras=self.cameras,
            shot_ids=self.shot_ids,
            rig_shots=self.rig_instances,
            rig_positions=self.instances_positions,
            rig_rotations=self.instances_rotations,
            rig_cameras=self.rig_cameras,
            reference=self.reference,
        )


class SyntheticInputData:
    """Class that generate all data necessary to run SfM processes end-to-end
    based on some input Reconstruction (synthetic or not), by re-creating inputs
    (GPS, projections) based on some required amount of noise.
    """

    reconstruction: types.Reconstruction
    exifs: Dict[str, Any]
    features: sd.SyntheticFeatures
    tracks_manager: pymap.TracksManager
    gcps: Dict[str, pymap.GroundControlPoint]

    def __init__(
        self,
        reconstruction: types.Reconstruction,
        reference: geo.TopocentricConverter,
        projection_max_depth: float,
        projection_noise: float,
        gps_noise: Union[Dict[str, float], float],
        imu_noise: float,
        gcp_noise: Tuple[float, float],
        causal_gps_noise: bool,
        gcps_count: Optional[int] = None,
        gcps_shift: Optional[NDArray] = None,
        on_disk_features_filename: Optional[str] = None,
        generate_projections: bool = True,
    ) -> None:
        self.reconstruction = reconstruction
        self.exifs = sg.generate_exifs(
            reconstruction,
            reference,
            gps_noise,
            imu_noise,
            causal_gps_noise=causal_gps_noise,
        )

        for shot in self.reconstruction.shots.values():
            shot.metadata = exif_to_metadata(
                self.exifs[shot.id], False, self.reconstruction.reference
            )

        if generate_projections:
            (self.features, self.tracks_manager, self.gcps) = sg.generate_track_data(
                reconstruction,
                projection_max_depth,
                projection_noise,
                gcp_noise,
                gcps_count,
                gcps_shift,
                on_disk_features_filename,
            )
        else:
            self.features = sd.SyntheticFeatures(None)
            self.tracks_manager = pymap.TracksManager()


def compare(
    reference: types.Reconstruction,
    gcps: Dict[str, pymap.GroundControlPoint],
    reconstruction: types.Reconstruction,
) -> Dict[str, float]:
    """Compare a reconstruction with reference groundtruth."""
    geo = reference.reference

    completeness = sm.completeness_errors(reference, reconstruction)

    geo_referenced = sm.change_geo_reference(reconstruction, geo.lat, geo.lon, geo.alt)
    absolute_position = sm.position_errors(reference, geo_referenced)
    absolute_rotation = sm.rotation_errors(reference, geo_referenced)
    absolute_points = sm.points_errors(reference, geo_referenced)
    absolute_gps = sm.gps_errors(geo_referenced)
    absolute_gcp = sm.gcp_errors(geo_referenced, gcps)

    aligned = sm.aligned_to_reference(reference, geo_referenced)
    aligned_position = sm.position_errors(reference, aligned)
    aligned_rotation = sm.rotation_errors(reference, aligned)
    aligned_points = sm.points_errors(reference, aligned)
    aligned_gps = sm.gps_errors(aligned)

    return {
        "ratio_cameras": completeness[0],
        "ratio_points": completeness[1],
        "absolute_position_rmse": sm.rmse(absolute_position),
        "absolute_position_mad": sm.mad(absolute_position),
        "absolute_rotation_rmse": sm.rmse(absolute_rotation),
        "absolute_rotation_median": np.median(absolute_rotation),
        "absolute_points_rmse": sm.rmse(absolute_points),
        "absolute_points_mad": sm.mad(absolute_points),
        "absolute_gps_rmse": sm.rmse(absolute_gps),
        "absolute_gps_mad": sm.mad(absolute_gps),
        "absolute_gcp_rmse_horizontal": sm.rmse(absolute_gcp[:, :2])
        if len(absolute_gcp.shape) > 1
        else 0.0,
        "absolute_gcp_rmse_vertical": sm.rmse(absolute_gcp[:, 2])
        if len(absolute_gcp.shape) > 1
        else 0.0,
        "aligned_position_rmse": sm.rmse(aligned_position),
        "aligned_position_mad": sm.mad(aligned_position),
        "aligned_rotation_rmse": sm.rmse(aligned_rotation),
        "aligned_rotation_median": np.median(aligned_rotation),
        "aligned_gps_rmse": sm.rmse(aligned_gps),
        "aligned_gps_mad": sm.mad(aligned_gps),
        "aligned_points_rmse": sm.rmse(aligned_points),
        "aligned_points_mad": sm.mad(aligned_points),
    }
