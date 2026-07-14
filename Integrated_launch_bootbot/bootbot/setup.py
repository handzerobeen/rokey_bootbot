from setuptools import find_packages, setup
from glob import glob

package_name = 'bootbot'

setup(
    name=package_name,
    version='0.0.0',

    packages=find_packages(exclude=['test']),

    data_files=[
        (
            'share/ament_index/resource_index/packages',
            ['resource/' + package_name]
        ),
        (
            'share/' + package_name,
            ['package.xml']
        ),
        (
            'share/' + package_name + '/launch',
            glob('launch/*.launch.py')
        ),


        # JSON 설정 파일
        (
            'share/' + package_name + '/data',
            glob('bootbot/data/*.json')
        ),

        # GUI HTML
        (
            'share/' + package_name + '/gui_web/templates',
            [
                'bootbot/gui_web/templates/landing.html',
                'bootbot/gui_web/templates/user_designing.html',
                'bootbot/gui_web/templates/manager_designing.html',
            ]
        ),

        # GUI CSS
        (
            'share/' + package_name + '/gui_web/static/css',
            [
                'bootbot/gui_web/static/css/style.css',
                'bootbot/gui_web/static/css/style_designing.css',
            ]
        ),

        # GUI JavaScript
        (
            'share/' + package_name + '/gui_web/static/js',
            [
                'bootbot/gui_web/static/js/main_designing.js',
                'bootbot/gui_web/static/js/database_designing.js',
                'bootbot/gui_web/static/js/tabs_designing.js',
                'bootbot/gui_web/static/js/manager_designing.js',
                'bootbot/gui_web/static/js/manager_viewer_designing.js',
                'bootbot/gui_web/static/js/manager_socket_designing.js',
            ]
        ),

        # 랜딩 페이지 배경 이미지
        (
            'share/' + package_name + '/gui_web/static/img',
            [
                'bootbot/gui_web/static/img/landing_bg.png',
            ]
        ),

        # 로봇 URDF
        (
            'share/' + package_name + '/gui_web/static/urdf',
            [
                'bootbot/gui_web/static/urdf/m0609_rg2.urdf',
            ]
        ),

        # M0609 mesh
        (
            'share/' + package_name
            + '/gui_web/static/urdf/meshes/m0609',
            [
                'bootbot/gui_web/static/urdf/meshes/m0609/MF0609_0_0.dae',
                'bootbot/gui_web/static/urdf/meshes/m0609/MF0609_1_0.dae',
                'bootbot/gui_web/static/urdf/meshes/m0609/MF0609_2_0.dae',
                'bootbot/gui_web/static/urdf/meshes/m0609/MF0609_2_1.dae',
                'bootbot/gui_web/static/urdf/meshes/m0609/MF0609_2_2.dae',
                'bootbot/gui_web/static/urdf/meshes/m0609/MF0609_3_0.dae',
                'bootbot/gui_web/static/urdf/meshes/m0609/MF0609_4_0.dae',
                'bootbot/gui_web/static/urdf/meshes/m0609/MF0609_4_1.dae',
                'bootbot/gui_web/static/urdf/meshes/m0609/MF0609_5_0.dae',
                'bootbot/gui_web/static/urdf/meshes/m0609/MF0609_6_0.dae',
            ]
        ),

        # RG2 mesh
        (
            'share/' + package_name
            + '/gui_web/static/urdf/meshes/rg2',
            [
                'bootbot/gui_web/static/urdf/meshes/rg2/base_link.stl',
                'bootbot/gui_web/static/urdf/meshes/rg2/inner_finger.stl',
                'bootbot/gui_web/static/urdf/meshes/rg2/inner_knuckle.stl',
                'bootbot/gui_web/static/urdf/meshes/rg2/outer_knuckle.stl',
            ]
        ),
    ],

    install_requires=[
        'setuptools',
    ],

    zip_safe=True,

    maintainer='woods',
    maintainer_email='woods@example.com',

    description=(
        'Calligraphy robot controller, server connector and web GUI'
    ),

    license='Apache-2.0',

    extras_require={
        'test': [
            'pytest',
        ],
    },

    entry_points={
        'console_scripts': [
            # 로봇 동작 파트
            'client_connector_node = bootbot.client_connector_node:main',
            'robot_write_drl_node = bootbot.robot_write_drl_node:main',

            'svg_to_robot = bootbot.svg_to_robot:main',

            'trigger_node = bootbot.trigger_node:main',

            # 서버 및 GUI
            'server_connector_node = '
            'bootbot.server_connector_node:main',

            'gui_web = bootbot.gui_web.app:main',
        ],
    },
)