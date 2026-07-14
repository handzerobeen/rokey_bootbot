from launch import LaunchDescription
from launch.actions import (
    ExecuteProcess,
    SetEnvironmentVariable,
    TimerAction,
)
from launch_ros.actions import Node


def generate_launch_description():
    server_connector = Node(
        package='bootbot',
        executable='server_connector_node',
        output='screen',
        parameters=[{
            'mongo_uri': 'mongodb://localhost:27017',
            'ws_host': '0.0.0.0',
            'ws_port': 8765,
            'robot_ns': 'dsr01',
            'posx_poll_period_sec': 0.1,
            'tcp_ref_coord': 101,
            'manager_poll_period_sec': 0.5,
        }],
    )

    delayed_gui = TimerAction(
        period=5.0,
        actions=[
            ExecuteProcess(
                cmd=[
                    'ros2',
                    'run',
                    'bootbot',
                    'gui_web',
                ],
                output='screen',
            ),
        ],
    )

    return LaunchDescription([
        SetEnvironmentVariable(
            name='WS_HOST',
            value='localhost',
        ),
        SetEnvironmentVariable(
            name='WS_PORT',
            value='8765',
        ),
        SetEnvironmentVariable(
            name='GUI_WEB_PORT',
            value='5000',
        ),
        SetEnvironmentVariable(
            name='MONGO_URI',
            value='mongodb://localhost:27017',
        ),

        server_connector,
        delayed_gui,
    ])