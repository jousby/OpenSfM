#include <bundle/error/projection_errors.h>
#include <gmock/gmock.h>
#include <gtest/gtest.h>

#include <Eigen/Dense>

class ReprojectionError2DFixtureBase : public ::testing::Test {
 public:
  using AScalar = Eigen::AutoDiffScalar<Eigen::VectorXd>;
  ReprojectionError2DFixtureBase() { observed << 0.5, 0.5; }

  constexpr static int size_residual = 2;
  constexpr static int size_point = 3;
  constexpr static int size_rt = 6;

  Vec2d observed;
  double scale{0.1};
  const double point[size_point] = {1.0, 2.0, 3.0};

  AScalar residual_adiff[size_residual];
  AScalar point_adiff[size_point];

  double residuals[size_residual];
  double jac_point[size_residual * size_point];
};

class ReprojectionError2DFixture : public ReprojectionError2DFixtureBase {
 public:
  void SetupADiff(int size, const double* camera, AScalar* camera_adiff) {
    const int total_size = size_point + size_rt + size_rt + size;
    for (int i = 0; i < size_point; ++i) {
      point_adiff[i].value() = point[i];
      point_adiff[i].derivatives() = VecXd::Unit(total_size, i);
    }
    for (int i = 0; i < size_rt; ++i) {
      rt_instance_adiff[i].value() = rt_instance[i];
      rt_instance_adiff[i].derivatives() =
          VecXd::Unit(total_size, size_point + i);
    }
    for (int i = 0; i < size_rt; ++i) {
      rt_camera_adiff[i].value() = rt_camera[i];
      rt_camera_adiff[i].derivatives() =
          VecXd::Unit(total_size, size_point + size_rt + i);
    }
    for (int i = 0; i < size; ++i) {
      camera_adiff[i].value() = camera[i];
      camera_adiff[i].derivatives() =
          VecXd::Unit(total_size, size_point + size_rt + size_rt + i);
    }
  }

  void CheckJacobians(int size, const double* jac_camera) {
    const double eps = 1e-14;
    for (int i = 0; i < size_residual; ++i) {
      for (int j = 0; j < size; ++j) {
        ASSERT_NEAR(
            residual_adiff[i].derivatives()(size_point + size_rt + size_rt + j),
            jac_camera[i * size + j], eps);
      }
    }
    for (int i = 0; i < size_residual; ++i) {
      for (int j = 0; j < size_point; ++j) {
        ASSERT_NEAR(residual_adiff[i].derivatives()(j),
                    jac_point[i * size_point + j], eps);
      }
    }
    for (int i = 0; i < size_residual; ++i) {
      for (int j = 0; j < size_rt; ++j) {
        ASSERT_NEAR(residual_adiff[i].derivatives()(size_point + j),
                    jac_rt_instance[i * size_rt + j], eps);
      }
    }
    for (int i = 0; i < size_residual; ++i) {
      for (int j = 0; j < size_rt; ++j) {
        ASSERT_NEAR(residual_adiff[i].derivatives()(size_point + size_rt + j),
                    jac_rt_camera[i * size_rt + j], eps);
      }
    }
  }

  template <int N>
  void RunTest(const geometry::ProjectionType& type, const double* camera) {
    constexpr int size = N;

    // Autodiff-ed version will be used as reference/expected values
    AScalar camera_adiff[size];
    SetupADiff(size, &camera[0], &camera_adiff[0]);
    bundle::ReprojectionError2D autodiff(type, observed, scale, true);
    autodiff(camera_adiff, rt_instance_adiff, rt_camera_adiff, point_adiff,
             residual_adiff);

    // We test for analytic evaluation
    double jac_camera[size_residual * size];
    const double* params[] = {camera, rt_camera, rt_instance, point};
    double* jacobians[] = {jac_camera, jac_rt_instance, jac_rt_camera,
                           jac_point};
    bundle::ReprojectionError2DAnalytic<size> analytic(type, observed, scale,
                                                       true);
    analytic.Evaluate(params, residuals, &jacobians[0]);

    // Check
    CheckJacobians(size, jac_camera);
  }

  const double rt_instance[size_rt] = {0.1, 0.2, 0.3, 0.4, 0.5, 0.6};
  AScalar rt_instance_adiff[size_rt];
  double jac_rt_instance[size_residual * size_rt];

  const double rt_camera[size_rt] = {0.1, 0.2, 0.3, 0.4, 0.5, 0.6};
  AScalar rt_camera_adiff[size_rt];
  double jac_rt_camera[size_residual * size_rt];
};

TEST_F(ReprojectionError2DFixture, BrownAnalyticErrorEvaluatesOK) {
  constexpr int size = 9;

  // focal, ar, cx, cy, k1, k2, k3, p1, p2
  constexpr std::array<double, size> camera{0.3,   1.0,   0.001,  -0.02, 0.1,
                                            -0.03, 0.001, -0.005, 0.001};
  RunTest<size>(geometry::ProjectionType::BROWN, camera.data());
}

TEST_F(ReprojectionError2DFixture, PerspectiveAnalyticErrorEvaluatesOK) {
  constexpr int size = 3;

  // focal, k1, k2
  constexpr std::array<double, size> camera{0.3, 0.1, -0.03};
  RunTest<size>(geometry::ProjectionType::PERSPECTIVE, camera.data());
}

TEST_F(ReprojectionError2DFixture, FisheyeAnalyticErrorEvaluatesOK) {
  constexpr int size = 3;

  // focal, k1, k2, k3
  constexpr std::array<double, size> camera{0.3, 0.1, -0.03};
  RunTest<size>(geometry::ProjectionType::FISHEYE, camera.data());
}

TEST_F(ReprojectionError2DFixture, FisheyeOpencvAnalyticErrorEvaluatesOK) {
  constexpr int size = 8;

  // focal, ar, cx, cy, k1, k2, k3, k4
  constexpr std::array<double, size> camera{0.3, 1.0,   0.001, -0.02,
                                            0.1, -0.03, 0.001, -0.005};
  RunTest<size>(geometry::ProjectionType::FISHEYE_OPENCV, camera.data());
}

TEST_F(ReprojectionError2DFixture, Fisheye62AnalyticErrorEvaluatesOK) {
  constexpr int size = 12;

  // focal, ar, cx, cy, k1, k2, k3, k4, k5, k6, p1, p2
  constexpr std::array<double, size> camera{0.3,  1.0,   0.001, -0.02,
                                            0.1,  -0.03, 0.001, -0.005,
                                            0.01, 0.006, 0.02,  0.003};
  RunTest<size>(geometry::ProjectionType::FISHEYE62, camera.data());
}

TEST_F(ReprojectionError2DFixture, Fisheye624AnalyticErrorEvaluatesOK) {
  constexpr int size = 16;

  // focal, ar, cx, cy, k1, k2, k3, k4, k5, k6, p1, p2, s0, s1, s2, s3
  constexpr std::array<double, size> camera{
      0.3,  1.0,   0.001, -0.02, 0.1,   -0.03,  0.001, -0.005,
      0.01, 0.006, 0.02,  0.003, 0.001, -0.009, -0.01, 0.03};
  RunTest<size>(geometry::ProjectionType::FISHEYE624, camera.data());
}

TEST_F(ReprojectionError2DFixture, DualAnalyticErrorEvaluatesOK) {
  constexpr int size = 4;

  // transition, focal, k1, k2
  constexpr std::array<double, size> camera{0.5, 0.3, 0.1, -0.03};
  RunTest<size>(geometry::ProjectionType::DUAL, camera.data());
}

class ReprojectionError3DFixture : public ::testing::Test {
 public:
  static constexpr int size = 3;

  using AScalar = Eigen::AutoDiffScalar<Eigen::VectorXd>;
  ReprojectionError3DFixture() { observed << 0.5, 0.5; }

  void SetupADiff() {
    const int total_size = size_point + size_rt + size_rt;
    for (int i = 0; i < size_point; ++i) {
      point_adiff[i].value() = point[i];
      point_adiff[i].derivatives() = VecXd::Unit(total_size, i);
    }
    for (int i = 0; i < size_rt; ++i) {
      rt_instance_adiff[i].value() = rt_instance[i];
      rt_instance_adiff[i].derivatives() =
          VecXd::Unit(total_size, size_point + i);
    }
    for (int i = 0; i < size_rt; ++i) {
      rt_camera_adiff[i].value() = rt_camera[i];
      rt_camera_adiff[i].derivatives() =
          VecXd::Unit(total_size, size_point + size_rt + i);
    }
  }

  void CheckJacobians() {
    const double eps = 1e-14;
    for (int i = 0; i < size; ++i) {
      for (int j = 0; j < size_point; ++j) {
        ASSERT_NEAR(residual_adiff[i].derivatives()(j),
                    jac_point[i * size_point + j], eps);
      }
    }
    for (int i = 0; i < size; ++i) {
      for (int j = 0; j < size_rt; ++j) {
        ASSERT_NEAR(residual_adiff[i].derivatives()(size_point + j),
                    jac_instance_rt[i * size_rt + j], eps);
      }
    }
    for (int i = 0; i < size; ++i) {
      for (int j = 0; j < size_rt; ++j) {
        ASSERT_NEAR(residual_adiff[i].derivatives()(size_point + size_rt + j),
                    jac_camera_rt[i * size_rt + j], eps);
      }
    }
  }

  constexpr static int size_point = 3;
  constexpr static int size_rt = 6;

  Vec2d observed;
  double scale{0.1};
  const double point[size_point] = {1.0, 2.0, 3.0};
  const double rt_instance[size_rt] = {0.1, 0.2, 0.3, 0.4, 0.5, 0.6};
  const double rt_camera[size_rt] = {0.1, 0.2, 0.3, 0.4, 0.5, 0.6};

  AScalar residual_adiff[size];
  AScalar point_adiff[size_point];
  AScalar rt_instance_adiff[size_rt];
  AScalar rt_camera_adiff[size_rt];

  double residuals[size];
  double jac_instance_rt[size * size_rt];
  double jac_camera_rt[size * size_rt];
  double jac_point[size * size_point];
};

TEST_F(ReprojectionError3DFixture, AnalyticErrorEvaluatesOK) {
  // Autodiff-ed version will be used as reference/expected values
  SetupADiff();
  AScalar dummy_adiff;
  bundle::ReprojectionError3D autodiff(geometry::ProjectionType::SPHERICAL,
                                       observed, scale, true);
  autodiff(&dummy_adiff, rt_instance_adiff, rt_camera_adiff, point_adiff,
           residual_adiff);

  // We test for analytic evaluation
  double dummy = 0.;
  double dummy_jac[] = {0., 0., 0.};
  const double* params[] = {&dummy, rt_instance, rt_camera, point};
  double* jacobians[] = {&dummy_jac[0], jac_instance_rt, jac_camera_rt,
                         jac_point};
  bundle::ReprojectionError3DAnalytic analytic(
      geometry::ProjectionType::SPHERICAL, observed, scale, true);
  analytic.Evaluate(params, residuals, &jacobians[0]);

  // Check
  CheckJacobians();
}
