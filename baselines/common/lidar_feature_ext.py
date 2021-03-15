import rospy
from sensor_msgs.msg import PointCloud2
from sensor_msgs.point_cloud2 import read_points
from visualization_msgs.msg import Marker
from visualization_msgs.msg import MarkerArray
from geometry_msgs.msg import Pose
from nav_msgs.msg import Odometry
from scipy.spatial.transform import Rotation as R
import open3d as o3d
import numpy as np
import math


class LidarFeatureExtract:

    number_of_features = 8
    bach_size_pc = 10

    def __init__(self, feature_size, bach_size_pc):
        self.pc_data = rospy.Subscriber("/os1_points", PointCloud2, self.store_lidar_data)
        self.pc_features_publisher = rospy.Publisher('lidar_features', MarkerArray, queue_size=16)
        self.batch_last_samples = np.empty((1,3), np.float32)
        self.size_batch = 0
        self.bach_size_pc = bach_size_pc
        self.number_of_features = feature_size
        self.extracted_features = np.full(self.number_of_features, 10.0)
        self.extracted_features_points = np.empty((3,3), np.int32)

    def store_lidar_data(self, data):
        points = np.array(list(read_points(data)))
        xyz = np.array([(x, y, z) for x, y, z, _, _ in points]) # assumes XYZIR

        if xyz.shape[0] > 0:
            xyz = self.filter_points(xyz, -10, 10)

            if self.size_batch >= self.bach_size_pc:
                #self.vis_points(self.batch_last_samples)
                self.batch_last_samples = np.delete(self.batch_last_samples , slice(0, xyz.shape[0]), axis=0)


            self.batch_last_samples = np.vstack([self.batch_last_samples, xyz])
            self.extracted_lidar_features()

            self.size_batch += 1


    def filter_points(self, xyz, min_axis, max_axis):
        #reduce computation time by removing points very far away
        xyz = np.delete(xyz, xyz[:,0] > max_axis, axis=0)
        xyz = np.delete(xyz, xyz[:,0] < min_axis, axis=0)
        xyz = np.delete(xyz, xyz[:,1] > max_axis, axis=0)
        xyz = np.delete(xyz, xyz[:,1] < min_axis, axis=0)
        #xyz = np.delete(xyz, xyz[:,2] > max_axis, axis=0)
        #xyz = np.delete(xyz, xyz[:,2] < min_axis, axis=0)

        return xyz

    def extracted_lidar_features(self):
        self.extracted_features_points = np.empty((3,3), np.int32)
        if len(self.batch_last_samples) > 0:
            pc = self.batch_last_samples #needed this due to concurrency issues
            index_sector = self.subdivide_pointcloud_to_sectors(self.batch_last_samples)
            for n in range(self.number_of_features):
                if (len(index_sector[n]) > 0):
                    sector = np.delete(pc, np.array(index_sector[n]), 0)
                    if sector.shape[0] > 0:
                        distance, closesd_p = self.get_distance_to_closest_point(sector)
                        self.extracted_features[n] = distance
                        self.extracted_features_points = np.vstack([self.extracted_features_points, closesd_p])

            #self.vis_points(sector)
        else:
            self.extracted_features = np.full(self.number_of_features, 10.0)

    def reset_lidar_storage(self):
        #clean all stored samples

        self.extracted_features_points = np.empty((3,3), np.int32)
        self.batch_last_samples = np.empty((0,3), np.float32)
        self.extracted_features = np.full(self.number_of_features, 10.0)
        self.size_batch = 0
        markerArray = MarkerArray()
        self.pc_features_publisher.publish(markerArray)


    def subdivide_pointcloud_to_sectors(self, pc):
        #divdes sphere of points in equal sectors

        i = 0
        index_sector = [[] for _ in range(self.number_of_features)]

        for xyz in pc:
            theta = math.atan2(xyz[1], xyz[0])
            theta = (theta + 2*math.pi) % (2*math.pi)

            r = math.sqrt(xyz[0]**2 + xyz[1]**2 + xyz[2]**2)
            phi = math.acos(xyz[2]/r)  # 90 deg is planar with rmf

            phi_bound = math.pi*(15/24) #phi boundary set to ca 113 deg
            pi_div = 2*math.pi/self.number_of_features #speed up comp

            for sliceN in range(self.number_of_features):
                if (theta < sliceN*pi_div or theta >= (sliceN+1)*pi_div) and phi < phi_bound:
                    index_sector[sliceN].append(i)
                    continue

            i += 1

        return index_sector

    def get_distance_to_closest_point(self, xyz):
        r = np.linalg.norm(xyz, axis=-1)
        index = np.where(r == np.min(r))

        return np.min(r), xyz[index[0][0]]


    def mark_feature_points(self, robot_odom, pc_points, reset):
        #We want to visualize the pc points extracted and used
        #as states in rviz.
        #input: robot_pose (for transformation purposes), pc_points(all feature points)

        markerArray = MarkerArray()
        MARKERS_MAX = self.number_of_features
        count = 0
        if reset:
            marker = Marker()
            marker.action = marker.DELETEALL
            markerArray.markers.append(marker)
            self.pc_features_publisher.publish(markerArray)

        if pc_points.size > 0:
            for xyz in pc_points:
                if xyz.size == 3:
                    if xyz[0] == 0.0 and xyz[1] == 0.0 and xyz[2] == 0.0:
                        continue #point does not exist and was set to 0,0,0 by default
                    point = Pose()
                    point.position.x = xyz[0]
                    point.position.y = xyz[1]
                    point.position.z = xyz[2]
                    point.orientation.x = 0
                    point.orientation.y = 0
                    point.orientation.z = 0
                    point.orientation.w = 1
                    world_point = self.transform_points_to_world_frame(robot_odom, point)

                    marker = Marker()
                    marker.header.frame_id = "world"
                    marker.type = marker.SPHERE
                    marker.action = marker.ADD
                    marker.scale.x = 0.2
                    marker.scale.y = 0.2
                    marker.scale.z = 0.2
                    marker.color.a = 1.0
                    marker.color.r = 0.0
                    marker.color.g = 0.0
                    marker.color.b = 1.0
                    marker.pose = world_point

                    # We add the new marker to the MarkerArray, removing the oldest
                    # marker from it when necessary
                    if (count > MARKERS_MAX):
                        markerArray.markers.pop(0)

                    markerArray.markers.append(marker)
                    # Renumber the marker IDs
                    id = 0
                    for m in markerArray.markers:
                        m.id = id
                        id += 1

                    # Publish the MarkerArray
                    self.pc_features_publisher.publish(markerArray)

                    count += 1

    # Input:    robot_odom  : Odometry()
    #           point        : Pose(), in vehicle frame
    # Return:   current_point  : Pose(), in world frame
    def transform_points_to_world_frame(self, robot_odom, feature_point):
        current_point = Pose()

        r_point = R.from_quat([feature_point.orientation.x, feature_point.orientation.y, feature_point.orientation.z, feature_point.orientation.w])
        point_euler_angles = r_point.as_euler('zyx', degrees=False)

        robot_pose = robot_odom.pose.pose
        r_robot = R.from_quat([robot_pose.orientation.x, robot_pose.orientation.y, robot_pose.orientation.z, robot_pose.orientation.w])
        robot_euler_angles = r_robot.as_euler('zyx', degrees=False)

        r_point_in_world = R.from_euler('z', point_euler_angles[0] + robot_euler_angles[0], degrees=False)
        point_pos_in_vehicle = np.array([feature_point.position.x, feature_point.position.y, feature_point.position.z])
        robot_pos = np.array([robot_pose.position.x, robot_pose.position.y, robot_pose.position.z])
        point_pos_in_world = R.from_euler('z', robot_euler_angles[0], degrees=False).as_matrix().dot(point_pos_in_vehicle) + robot_pos
        # print('R abc:', R.from_euler('z', robot_euler_angles[0], degrees=False).as_matrix())
        #print('point_pos_in_vehicle:', point_pos_in_vehicle)
        #print('robot_pos:', robot_pos)
        #print('point_pos_in_world:', point_pos_in_world)

        current_point.position.x = point_pos_in_world[0]
        current_point.position.y = point_pos_in_world[1]
        current_point.position.z = point_pos_in_world[2]

        current_point_quat = r_point_in_world.as_quat()
        current_point.orientation.x = current_point_quat[0]
        current_point.orientation.y = current_point_quat[1]
        current_point.orientation.z = current_point_quat[2]
        current_point.orientation.w = current_point_quat[3]

        return current_point

    def vis_points(self, pc):
        #visualize points with open3d
        pcd = o3d.geometry.PointCloud()
        pcd.points = o3d.utility.Vector3dVector(pc)

        #adding colors
        #rgb = np.asarray([0.0, 255.0, 0.0])
        #rgb_t = np.transpose(rgb)/255.0
        #pcd.colors = o3d.utility.Vector3dVector([rgb_t, rgb_t, rgb_t, rgb_t])

        o3d.visualization.draw_geometries([pcd])
