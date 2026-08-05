# from django.db.models.signals import post_save, post_migrate
# from django.contrib.auth import get_user_model
# from django.contrib.auth.models import Group, Permission
# from django.core.exceptions import ObjectDoesNotExist
# from django.dispatch import receiver
# from datetime import time

# from .models import *

# DEVELOPMENT = False


# # cria automaticamente o objeto CurrentWorkStationParameters contendo apenas a id_work_station
# # cria automaticamente um objeto ProductionShiftRoutine para cada turno de produção contendendo apenas id_work_station e id_production_shift
# @receiver(post_save, sender=WorkStation)
# def create_current_work_station_parameters(sender, instance, created, **kwargs):
#     if created:
#         CurrentWorkStationParameters.objects.create(
#             id_work_station=instance,
#         )

#         production_shifts = ProductionShift.objects.all()
#         for shift in production_shifts:
#             ProductionShiftRoutines.objects.create(
#                 id_production_shift=shift,
#                 id_work_station=instance
#             )
            

# # cria os grupos automaticamente, mas é preciso customizar as permissões para cada grupo
# # cria os turnos, parâmetros das restrições e "cargo de trabalho" INSS e Férias
# @receiver(post_migrate)
# def create_initial_configs(sender, **kwargs):
#     if sender.name == "ph_app":

#         # Para todo migrate é verificado se o o superusuário existe, caso não, ele é criado
#         User = get_user_model()
#         if not User.objects.filter(username="Admin").exists():
#             adm_user = User.objects.create_superuser(
#                 username="Admin", password="@Admin123", first_name="Admin"
#             )

#         # Para todo migrate é verificado se os grupos da lista abaixo já existem, caso não, são criadas
#         group_names = ["Administradores", "Operadores"]
#         for group_name in group_names:
#             if not Group.objects.filter(name=group_name).exists():
#                 group = Group.objects.create(name=group_name)

#                 if group_name == "Administradores":

#                     # Adiciona todas as permissões disponíveis ao grupo "Administradores"
#                     all_permissions = Permission.objects.all()
#                     group.permissions.set(all_permissions)

#                     # Adiciona o admin no grupo Administradores
#                     adm_user.groups.add(group)

#                 elif group_name == "Operadores":
#                     pass

#         # Cria os campos de MQTT para serem preenchidos com o caminho do broker e a porta utilizada
#         if not Settings.objects.filter(parameter_name="Endereço Broker MQTT").exists():
#             Settings.objects.get_or_create(
#                 parameter_name="Endereço Broker MQTT",
#             )
#         if not Settings.objects.filter(parameter_name="Porta Broker MQTT").exists():
#             Settings.objects.get_or_create(
#                 parameter_name="Porta Broker MQTT",
#             )

#         # Cria os 3 turnos de maneira automática, para que não seja esquecido
#         if not ProductionShift.objects.filter(shift_name="Primeiro Turno").exists():
#             ProductionShift.objects.create(
#                 shift_name="Primeiro Turno",
#                 start_shift_hour=time(6, 0),
#                 end_shift_hour=time(15, 0),
#                 scheduled_stop_hours=1,
#                 average_employee_cost=5036.00,
#                 shift_number=1,
#             )
#         if not ProductionShift.objects.filter(shift_name="Segundo Turno").exists():
#             ProductionShift.objects.create(
#                 shift_name="Segundo Turno",
#                 start_shift_hour=time(15, 0),
#                 end_shift_hour=time(23, 59),
#                 scheduled_stop_hours=1.00,
#                 average_employee_cost=5036.00,
#                 shift_number=2,
#             )
#         if not ProductionShift.objects.filter(shift_name="Terceiro Turno").exists():
#             ProductionShift.objects.create(
#                 shift_name="Terceiro Turno",
#                 start_shift_hour=time(0, 0),
#                 end_shift_hour=time(6, 0),
#                 scheduled_stop_hours=0,
#                 average_employee_cost=6623.60,
#                 shift_number=3,
#             )

#         # Para todo migrate é verificado se as intervenções da lista abaixo já existem, caso não, são criadas
#         interventions = [
#             "Manutenção Corretiva",
#             "Setup",
#             "Falta de Matéria Prima",
#             "Manutenção Preventiva",
#             "Troca de Ferramenta",
#             "Ajuste de Qualidade",
#             "Troca de Turno",
#             "Café",
#             "Refeição",
#             "Necessidades Pessoais",
#         ]

#         # Identificadores para cada tipo de intervenção
#         identification_numbers = range(0, 10)

#         # Verifica e cria os tipos de intervenção
#         for intervention, identification_number in zip(
#             interventions, identification_numbers
#         ):
#             if not InterventionType.objects.filter(
#                 identification_number=identification_number
#             ).exists():
#                 InterventionType.objects.create(
#                     intervention_name=intervention,
#                     identification_number=identification_number,
#                 )

#         # MARK:Atenção
#         # Daqui para baixo são todas configurações de para criação de parâmetros de teste
#         if DEVELOPMENT:

#             # Cria o usúario Gabriel e adciona ele no grupo de Operadores
#             if not User.objects.filter(username="Gabriel").exists():
#                 op_user = User.objects.create_user(
#                     username="Gabriel",
#                     password="sanches123",
#                     first_name="Gabriel",
#                     last_name="Sanches",
#                 )
#                 group = Group.objects.get(name="Operadores")
#                 op_user.groups.add(group)

#             if not PartNumber.objects.filter(
#                 part_number_name="Part Number de teste 1"
#             ).exists():
#                 PartNumber.objects.get_or_create(
#                     part_number_name="Part Number de teste 1",
#                 )
#             if not PartNumber.objects.filter(
#                 part_number_name="Part Number de teste 2"
#             ).exists():
#                 PartNumber.objects.get_or_create(
#                     part_number_name="Part Number de teste 2",
#                 )

#             if not WorkStation.objects.filter(
#                 work_station_name="Máquina de teste 1"
#             ).exists():
#                 WorkStation.objects.get_or_create(
#                     work_station_name="Máquina de teste 1",
#                 )
#             if not WorkStation.objects.filter(
#                 work_station_name="Máquina de teste 2"
#             ).exists():
#                 WorkStation.objects.get_or_create(
#                     work_station_name="Máquina de teste 2",
#                 )
#             if not WorkStation.objects.filter(
#                 work_station_name="Máquina de teste 3"
#             ).exists():
#                 WorkStation.objects.get_or_create(
#                     work_station_name="Máquina de teste 3",
#                 )

#             # Verifique se existem instâncias dos modelos relacionados com os nomes especificados
#             pn1_instance = PartNumber.objects.get(
#                 part_number_name="Part Number de teste 1"
#             )
#             pn2_instance = PartNumber.objects.get(
#                 part_number_name="Part Number de teste 2"
#             )

#             ws1_instance = WorkStation.objects.get(
#                 work_station_name="Máquina de teste 1"
#             )
#             ws2_instance = WorkStation.objects.get(
#                 work_station_name="Máquina de teste 2"
#             )
#             ws3_instance = WorkStation.objects.get(
#                 work_station_name="Máquina de teste 3"
#             )

#             operation1 = "Desbaste diâmetro externo"
#             operation2 = "Desbaste diâmetro interno"
#             operation3 = "Acabamento"

#             # Criando o Roteiro de Produção para o Part Number de teste 1
#             try:
#                 if not Operation.objects.filter(
#                     id_work_station=ws1_instance,
#                     id_part_number=pn1_instance,
#                     operation_name=operation1
#                 ).exists():
#                     Operation.objects.create(
#                         id_work_station=ws1_instance,
#                         id_part_number=pn1_instance,
#                         operation_name=operation1,
#                         default_cycle_time=12,
#                     )

#                 if not Operation.objects.filter(
#                     id_work_station=ws1_instance,
#                     id_part_number=pn1_instance,
#                     operation_name=operation2
#                 ).exists():
#                     Operation.objects.create(
#                         id_work_station=ws1_instance,
#                         id_part_number=pn1_instance,
#                         operation_name=operation2,
#                         default_cycle_time=12,
#                     )

#                 if not Operation.objects.filter(
#                     id_work_station=ws2_instance,
#                     id_part_number=pn1_instance,
#                     operation_name=operation2
#                 ).exists():
#                     Operation.objects.create(
#                         id_work_station=ws2_instance,
#                         id_part_number=pn1_instance,
#                         operation_name=operation2,
#                         default_cycle_time=15,
#                     )

#                 if not Operation.objects.filter(
#                     id_work_station=ws3_instance,
#                     id_part_number=pn1_instance,
#                     operation_name=operation3
#                 ).exists():
#                     Operation.objects.create(
#                         id_work_station=ws3_instance,
#                         id_part_number=pn1_instance,
#                         operation_name=operation3,
#                         default_cycle_time=22,
#                     )

#                 # Criando o Roteiro de Produção para o Part Number de teste 2
#                 if not Operation.objects.filter(
#                     id_work_station=ws1_instance,
#                     id_part_number=pn2_instance,
#                     operation_name=operation1
#                 ).exists():
#                     Operation.objects.create(
#                         id_work_station=ws1_instance,
#                         id_part_number=pn2_instance,
#                         operation_name=operation1,
#                         default_cycle_time=12,
#                     )

#                 if not Operation.objects.filter(
#                     id_work_station=ws2_instance,
#                     id_part_number=pn2_instance,
#                     operation_name=operation3
#                 ).exists():
#                     Operation.objects.create(
#                         id_work_station=ws2_instance,
#                         id_part_number=pn2_instance,
#                         operation_name=operation3,
#                         default_cycle_time=12,
#                     )

#                 if not Operation.objects.filter(
#                     id_work_station=ws3_instance,
#                     id_part_number=pn2_instance,
#                     operation_name=operation3
#                 ).exists():
#                     Operation.objects.create(
#                         id_work_station=ws3_instance,
#                         id_part_number=pn2_instance,
#                         operation_name=operation3,
#                         default_cycle_time=12,
#                     )
#             except:
#                 print("O unique não deixa fazer o migrate de novo para essa parte")