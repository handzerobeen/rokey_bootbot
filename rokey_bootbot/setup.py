from setuptools import find_packages, setup

package_name = 'rokey_bootbot'

setup(
    name=package_name,
    version='0.0.0',
    packages=find_packages(exclude=['test']),
    data_files=[
        ('share/ament_index/resource_index/packages',
            ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
        ('share/' + package_name + '/gui_web/templates', [
            'rokey_bootbot/gui_web/templates/landing.html',
            'rokey_bootbot/gui_web/templates/user_designing.html',
            'rokey_bootbot/gui_web/templates/manager_designing.html',
        ]),
        ('share/' + package_name + '/gui_web/static/css', [
            'rokey_bootbot/gui_web/static/css/style.css',
            'rokey_bootbot/gui_web/static/css/style_designing.css',
        ]),
        ('share/' + package_name + '/gui_web/static/js', [
            'rokey_bootbot/gui_web/static/js/main_designing.js',
            'rokey_bootbot/gui_web/static/js/database_designing.js',
            'rokey_bootbot/gui_web/static/js/tabs_designing.js',
            'rokey_bootbot/gui_web/static/js/manager_designing.js',
            'rokey_bootbot/gui_web/static/js/manager_viewer_designing.js',
            'rokey_bootbot/gui_web/static/js/manager_socket_designing.js',
        ]),
        # 랜딩 페이지(/) 배경 이미지.
        ('share/' + package_name + '/gui_web/static/img', [
            'rokey_bootbot/gui_web/static/img/landing_bg.png',
        ]),
        # MANAGER 3D 뷰어 프로토타입용 정적 URDF (m0609_rg2_bringup의
        # m0609_with_rg2.urdf.xacro를 xacro로 펼치고 collision을 제거, mesh 경로를
        # package://에서 상대경로로 바꾼 사본 -- 브라우저는 xacro/package://를 못 읽음).
        ('share/' + package_name + '/gui_web/static/urdf', [
            'rokey_bootbot/gui_web/static/urdf/m0609_rg2.urdf',
        ]),
        ('share/' + package_name + '/gui_web/static/urdf/meshes/m0609', [
            'rokey_bootbot/gui_web/static/urdf/meshes/m0609/MF0609_0_0.dae',
            'rokey_bootbot/gui_web/static/urdf/meshes/m0609/MF0609_1_0.dae',
            'rokey_bootbot/gui_web/static/urdf/meshes/m0609/MF0609_2_0.dae',
            'rokey_bootbot/gui_web/static/urdf/meshes/m0609/MF0609_2_1.dae',
            'rokey_bootbot/gui_web/static/urdf/meshes/m0609/MF0609_2_2.dae',
            'rokey_bootbot/gui_web/static/urdf/meshes/m0609/MF0609_3_0.dae',
            'rokey_bootbot/gui_web/static/urdf/meshes/m0609/MF0609_4_0.dae',
            'rokey_bootbot/gui_web/static/urdf/meshes/m0609/MF0609_4_1.dae',
            'rokey_bootbot/gui_web/static/urdf/meshes/m0609/MF0609_5_0.dae',
            'rokey_bootbot/gui_web/static/urdf/meshes/m0609/MF0609_6_0.dae',
        ]),
        ('share/' + package_name + '/gui_web/static/urdf/meshes/rg2', [
            'rokey_bootbot/gui_web/static/urdf/meshes/rg2/base_link.stl',
            'rokey_bootbot/gui_web/static/urdf/meshes/rg2/inner_finger.stl',
            'rokey_bootbot/gui_web/static/urdf/meshes/rg2/inner_knuckle.stl',
            'rokey_bootbot/gui_web/static/urdf/meshes/rg2/outer_knuckle.stl',
        ]),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='son',
    maintainer_email='son@todo.todo',
    description='TODO: Package description',
    license='Apache-2.0',
    extras_require={
        'test': [
            'pytest',
        ],
    },
    entry_points={
        'console_scripts': [
            # '_node'로 끝나는 실행파일 = 실제 ROS2 노드. gui_web은 rclpy를 쓰지 않는 순수
            # Flask 프로세스라 노드가 아니므로 접미사를 붙이지 않는다.
            'server_connector_node = rokey_bootbot.server_connector_node:main',
            # server_connector_node_designing: server_connector_node.py의 다음 실험
            # 대상 복제본. MANAGER 대시보드 확장 + 재실행 직접발행 등은 이미 검증되어
            # server_connector_node.py에 반영됐고(2026-07-09), 이 파일은 다음 실험을
            # 위해 계속 유지한다.
            'server_connector_node_designing = rokey_bootbot.server_connector_node_designing:main',
            'gui_web = rokey_bootbot.gui_web.app:main',
        ],
    },
)
