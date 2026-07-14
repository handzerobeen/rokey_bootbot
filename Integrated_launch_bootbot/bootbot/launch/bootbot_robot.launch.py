from launch import LaunchDescription
from launch.actions import TimerAction
from launch_ros.actions import Node


def generate_launch_description():
    robot_write_node = Node(
        package='bootbot',
        executable='robot_write_drl_node',
        output='screen',
    )

    delayed_client_connector = TimerAction(
        period=3.0,
        actions=[
            Node(
                package='bootbot',
                executable='client_connector_node',
                output='screen',
            ),
        ],
    )

    delayed_trigger = TimerAction(
        period=6.0,
        actions=[
            Node(
                package='bootbot',
                executable='trigger_node',
                output='screen',
                emulate_tty=True,
            ),
        ],
    )

    return LaunchDescription([
        robot_write_node,
        delayed_client_connector,
        delayed_trigger,
    ])